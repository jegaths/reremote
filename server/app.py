"""ReRemote web server: FastAPI + SQLite + pyserial.

Run:  uvicorn app:app --reload          (from the server/ directory)
  or: python app.py [--db path] [--simulate]
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from capture import CaptureEngine, EventHub, list_serial_ports
from db import Database
from decoder import decode
from matcher import match_capture
import firmware as fw
from ha import ACTIONS, HomeAssistant, action_to_call, generate_config_h
import ota

HERE = Path(__file__).parent
DB_PATH = os.environ.get("REREMOTE_DB", str(HERE.parent / "data" / "remotes.sqlite"))
Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("app")

db = Database(DB_PATH)
hub = EventHub()


def _on_engine_event(ev: dict[str, Any]) -> None:
    if ev.get("type") == "capture":
        match = match_capture(ev, db.buttons_with_samples())
        if engine.source == "simulator":
            # fake presses are shown live but never stored next to real ones
            ev = {**ev, "id": None, "simulated": True, "match": match}
        else:
            row = db.log_capture(
                timings=ev["timings"], fingerprint=ev["fingerprint"], repeats=ev["repeats"],
                protocol=ev["protocol"], code=ev["code"], bit_length=ev["bit_length"],
                pulse_length=ev["pulse_length"], button_id=match["button_id"] if match else None,
            )
            if match and match["method"] == "pattern" and match["sample_count"] < 32:
                # every recognised press refines what we know about the button
                db.add_sample(match["button_id"], ev["timings"], ev["fingerprint"], ev["code"])
                match = {**match, "sample_count": match["sample_count"] + 1, "learned": True}
            ev = {**ev, "id": row["id"], "captured_at": row["captured_at"], "match": match}
    hub.publish(ev)


engine = CaptureEngine(on_event=_on_engine_event)
builder = fw.BuildRunner(on_event=hub.publish)

SECRET_KEYS = ("ha_token", "wifi_pass", "ota_token")


def _settings() -> dict[str, str]:
    """Stored settings plus defaults; makes sure the bridge has an OTA token."""
    st = db.get_settings()
    if not st.get("ota_token"):
        st = db.set_settings({"ota_token": ota.new_token()})
    st.setdefault("bridge_host", ota.DEFAULT_HOST)
    return st


def _public_settings() -> dict[str, Any]:
    st = _settings()
    out: dict[str, Any] = {k: v for k, v in st.items() if k not in SECRET_KEYS}
    for k in SECRET_KEYS:
        out[k + "_set"] = bool(st.get(k))
    return out


def _ha() -> HomeAssistant:
    st = db.get_settings()
    return HomeAssistant(st.get("ha_url", ""), st.get("ha_token", ""))


def _write_generated_config(sketch: str) -> None:
    """Regenerate config.h for sketches that declare a config generator."""
    m = next((x for x in fw.list_sketches() if x["name"] == sketch), None)
    if not m or m.get("config") != "ha_bridge":
        return
    text = generate_config_h(_settings(), db.buttons_with_samples(), db.list_actions())
    (fw.sketch_dir(sketch) / "config.h").write_text(text)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    hub.loop = asyncio.get_running_loop()
    if os.environ.get("REREMOTE_SIMULATE") == "1":
        engine.start_simulator()
    yield
    engine.disconnect()


app = FastAPI(title="ReRemote", version="1.0.0", lifespan=lifespan)
_server: Any = None     # the uvicorn Server when started via `python app.py`


def _shutting_down() -> bool:
    return bool(_server is not None and getattr(_server, "should_exit", False))


# ---- static ------------------------------------------------------------------
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(HERE / "static" / "index.html")


# ---- connection --------------------------------------------------------------
class ConnectBody(BaseModel):
    port: str
    baud: int = 115200


@app.get("/api/ports")
async def api_ports() -> list[dict[str, str]]:
    ports = list_serial_ports()
    for p in ports:
        p["board"] = fw.guess_board(p["description"])
    return ports


@app.get("/api/status")
async def api_status() -> dict[str, Any]:
    return engine.status()


def _connect_with_retry(port: str, baud: int, attempts: int = 3) -> None:
    """The ESP32-S3's native USB port often drops the very first open after
    another process released it; a second try a moment later succeeds."""
    last: Optional[Exception] = None
    for i in range(attempts):
        try:
            engine.connect(port, baud)
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(0.7)
            continue
        time.sleep(0.8)
        if engine.status()["connected"]:
            return
        last = RuntimeError("port closed right after opening")
        time.sleep(0.5)
    raise last or RuntimeError("could not connect")


@app.post("/api/connect")
async def api_connect(body: ConnectBody) -> dict[str, Any]:
    try:
        await asyncio.to_thread(_connect_with_retry, body.port, body.baud)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"could not open {body.port}: {e}") from e
    return engine.status()


@app.post("/api/simulate")
async def api_simulate() -> dict[str, Any]:
    await asyncio.to_thread(engine.start_simulator)
    return engine.status()


@app.post("/api/disconnect")
async def api_disconnect() -> dict[str, Any]:
    await asyncio.to_thread(engine.disconnect)
    return engine.status()


class CommandBody(BaseModel):
    command: str = Field(min_length=1, max_length=20)


@app.post("/api/command")
async def api_command(body: CommandBody) -> dict[str, Any]:
    ok = engine.send_command(body.command)
    if not ok:
        raise HTTPException(400, "not connected to a serial port")
    return {"sent": body.command}


class SettingsBody(BaseModel):
    min_repeats: Optional[int] = Field(default=None, ge=1, le=20)
    settle_ms: Optional[int] = Field(default=None, ge=50, le=5000)


@app.post("/api/settings")
async def api_settings(body: SettingsBody) -> dict[str, Any]:
    if body.min_repeats is not None:
        engine.min_repeats = body.min_repeats
    if body.settle_ms is not None:
        engine.settle_ms = body.settle_ms
    return engine.status()


# ---- events (SSE) ------------------------------------------------------------
@app.get("/api/events")
async def api_events() -> StreamingResponse:
    q = hub.subscribe()

    async def gen():
        # The stream ends by itself when the server is stopping; otherwise uvicorn
        # would wait for the browser to close it before shutting down.
        quiet = 0.0
        try:
            yield EventHub.format_sse({"type": "status", **engine.status()})
            while not _shutting_down():
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=1)
                    quiet = 0.0
                    yield EventHub.format_sse(ev)
                except asyncio.TimeoutError:
                    quiet += 1
                    if quiet >= 15:
                        quiet = 0.0
                        yield ": keepalive\n\n"
        finally:
            hub.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---- remotes -----------------------------------------------------------------
class RemoteBody(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    brand: str = ""
    model: str = ""
    technology: str = "rf433"
    notes: str = ""


class RemotePatch(BaseModel):
    name: Optional[str] = None
    brand: Optional[str] = None
    model: Optional[str] = None
    technology: Optional[str] = None
    notes: Optional[str] = None


@app.get("/api/remotes")
async def api_remotes() -> list[dict[str, Any]]:
    return db.list_remotes()


@app.post("/api/remotes", status_code=201)
async def api_create_remote(body: RemoteBody) -> dict[str, Any]:
    if any(r["name"].lower() == body.name.strip().lower() for r in db.list_remotes()):
        raise HTTPException(409, "a remote with that name already exists")
    return db.create_remote(**body.model_dump())


@app.patch("/api/remotes/{remote_id}")
async def api_update_remote(remote_id: int, body: RemotePatch) -> dict[str, Any]:
    r = db.update_remote(remote_id, **body.model_dump())
    if not r:
        raise HTTPException(404, "remote not found")
    return r


@app.delete("/api/remotes/{remote_id}")
async def api_delete_remote(remote_id: int) -> dict[str, bool]:
    if not db.delete_remote(remote_id):
        raise HTTPException(404, "remote not found")
    return {"deleted": True}


@app.get("/api/remotes/{remote_id}/buttons")
async def api_remote_buttons(remote_id: int) -> list[dict[str, Any]]:
    if not db.get_remote(remote_id):
        raise HTTPException(404, "remote not found")
    return db.list_buttons(remote_id)


# ---- buttons -----------------------------------------------------------------
class ButtonBody(BaseModel):
    remote_id: int
    name: str = Field(min_length=1, max_length=80)
    capture_id: Optional[int] = None
    timings: Optional[list[int]] = None
    repeats: int = 1
    notes: str = ""


class ButtonPatch(BaseModel):
    name: str = Field(min_length=1, max_length=80)


@app.get("/api/buttons")
async def api_buttons() -> list[dict[str, Any]]:
    return db.list_buttons()


@app.post("/api/buttons", status_code=201)
async def api_create_button(body: ButtonBody) -> dict[str, Any]:
    if not db.get_remote(body.remote_id):
        raise HTTPException(404, "remote not found")
    timings = body.timings
    repeats = body.repeats
    if body.capture_id is not None:
        cap = db.get_capture(body.capture_id)
        if not cap:
            raise HTTPException(404, "capture not found")
        timings, repeats = cap["timings"], cap["repeats"]
    if not timings or len(timings) < 4:
        raise HTTPException(400, "timings or capture_id required")
    d = decode(timings)
    existing = db.find_button_by_fingerprint(d.fingerprint)
    if existing:
        raise HTTPException(
            409, f"this code is already saved as '{existing['remote_name']} / {existing['name']}'")
    if any(b["name"].lower() == body.name.strip().lower() for b in db.list_buttons(body.remote_id)):
        raise HTTPException(409, "that remote already has a button with this name")
    btn = db.create_button(
        remote_id=body.remote_id, name=body.name, timings=timings, fingerprint=d.fingerprint,
        protocol=d.protocol, code=d.code, bit_length=d.bit_length,
        pulse_length=d.pulse_length, repeats=repeats, notes=body.notes,
    )
    if body.capture_id is not None:
        db.link_capture(body.capture_id, btn["id"])
    return btn


class SampleBody(BaseModel):
    capture_id: Optional[int] = None
    timings: Optional[list[int]] = None


@app.post("/api/buttons/{button_id}/samples", status_code=201)
async def api_add_sample(button_id: int, body: SampleBody) -> dict[str, Any]:
    """Teach a button another code: 'this press is also <button>'."""
    btn = db.get_button(button_id)
    if not btn:
        raise HTTPException(404, "button not found")
    timings = body.timings
    if body.capture_id is not None:
        cap = db.get_capture(body.capture_id)
        if not cap:
            raise HTTPException(404, "capture not found")
        timings = cap["timings"]
    if not timings or len(timings) < 4:
        raise HTTPException(400, "timings or capture_id required")
    d = decode(timings)
    if d.protocol != btn["protocol"] or d.bit_length != btn["bit_length"]:
        raise HTTPException(409, f"this press decodes as {d.protocol}/{d.bit_length} bits, "
                                 f"the button is {btn['protocol']}/{btn['bit_length']} bits")
    other = db.find_button_by_fingerprint(d.fingerprint)
    if other and other["id"] != button_id:
        raise HTTPException(409, f"this code already belongs to '{other['remote_name']} / {other['name']}'")
    if not other:
        db.add_sample(button_id, timings, d.fingerprint, d.code)
    if body.capture_id is not None:
        db.link_capture(body.capture_id, button_id)
    out = db.get_button(button_id)
    codes = [smp["code"] for smp in db.list_samples(button_id) if smp["code"] is not None]
    if len(codes) >= 2 and out["bit_length"]:
        from matcher import stable_mask, widen_to_nibbles
        m = widen_to_nibbles(stable_mask(codes, out["bit_length"]), out["bit_length"])
        out["stable_mask_hex"] = f"0x{m:X}"
        out["stable_bits"] = bin(m).count("1")
    return out


@app.get("/api/buttons/{button_id}/samples")
async def api_list_samples(button_id: int) -> list[dict[str, Any]]:
    if not db.get_button(button_id):
        raise HTTPException(404, "button not found")
    return db.list_samples(button_id)


@app.patch("/api/buttons/{button_id}")
async def api_rename_button(button_id: int, body: ButtonPatch) -> dict[str, Any]:
    b = db.rename_button(button_id, body.name)
    if not b:
        raise HTTPException(404, "button not found")
    return b


@app.delete("/api/buttons/{button_id}")
async def api_delete_button(button_id: int) -> dict[str, bool]:
    if not db.delete_button(button_id):
        raise HTTPException(404, "button not found")
    return {"deleted": True}


# ---- serial log --------------------------------------------------------------
@app.get("/api/log")
async def api_log(limit: int = 500, frames: bool = True) -> list[dict[str, Any]]:
    items = list(engine.log)
    if not frames:
        items = [x for x in items if x["kind"] != "frame"]
    return items[-min(max(limit, 1), 2000):]


@app.delete("/api/log")
async def api_clear_log() -> dict[str, bool]:
    engine.log.clear()
    return {"cleared": True}


# ---- firmware: sketches, files, builds --------------------------------------
class SketchBody(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    title: str = ""
    description: str = ""
    copy_from: Optional[str] = None


class FileBody(BaseModel):
    content: str


class BuildBody(BaseModel):
    sketch: str
    board: str
    action: str = "flash"          # compile | flash
    port: Optional[str] = None
    reconnect: bool = True         # reopen the serial port in the capture engine afterwards


@app.get("/api/firmware")
async def api_sketches() -> dict[str, Any]:
    return {"sketches": fw.list_sketches(), "boards": fw.BOARD_LABEL,
            "arduino_cli": bool(fw.arduino_cli()), "building": builder.current}


@app.post("/api/firmware", status_code=201)
async def api_create_sketch(body: SketchBody) -> dict[str, Any]:
    try:
        return fw.create_sketch(body.name, body.title, body.description, body.copy_from)
    except FileExistsError:
        raise HTTPException(409, "a sketch with that name already exists")
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.delete("/api/firmware/{sketch}")
async def api_delete_sketch(sketch: str) -> dict[str, bool]:
    try:
        fw.delete_sketch(sketch)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"deleted": True}


@app.get("/api/firmware/{sketch}/files/{filename}", response_class=PlainTextResponse)
async def api_read_file(sketch: str, filename: str) -> str:
    try:
        if filename == "config.h":
            _write_generated_config(sketch)   # show what the next build will use
        return fw.read_file(sketch, filename)
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(404, str(e))


@app.put("/api/firmware/{sketch}/files/{filename}")
async def api_write_file(sketch: str, filename: str, body: FileBody) -> dict[str, Any]:
    try:
        fw.write_file(sketch, filename, body.content)
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(400, str(e))
    return {"saved": filename, "files": fw.sketch_files(sketch)}


@app.delete("/api/firmware/{sketch}/files/{filename}")
async def api_delete_file(sketch: str, filename: str) -> dict[str, Any]:
    try:
        fw.delete_file(sketch, filename)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"deleted": filename, "files": fw.sketch_files(sketch)}


@app.post("/api/firmware/build")
async def api_build(body: BuildBody) -> dict[str, Any]:
    was_connected = engine.status()["connected"] and engine.source == "serial"
    port = body.port or (engine.port if engine.source == "serial" else None)
    engine_port = engine.port if engine.source == "serial" else None
    over_the_air = fw.is_ota_port(port)

    if over_the_air:
        # compile here, push the binary to the bridge over WiFi; the serial port is not involved
        st = _settings()
        host, token = fw.ota_host(port), st["ota_token"]

        def uploader(bin_path: Path, emit: Any) -> None:
            ota.upload(host, token, bin_path, emit)

        try:
            return builder.start(body.sketch, body.board, body.action, port,
                                 before=lambda: _write_generated_config(body.sketch), uploader=uploader)
        except (ValueError, RuntimeError, FileNotFoundError) as e:
            raise HTTPException(400, str(e))

    def before() -> None:
        _write_generated_config(body.sketch)
        if body.action == "flash" and engine.source == "serial":
            engine.disconnect()           # esptool needs the port to itself
            time.sleep(0.5)

    def after(ok: bool) -> None:
        if body.action != "flash" or not body.reconnect:
            return
        target = port or engine_port
        if not target:
            return
        # the board re-enumerates after a flash; give it a moment, retry a few times
        for _ in range(6):
            time.sleep(1.5)
            try:
                _connect_with_retry(target, 115200, attempts=2)
                return
            except Exception:  # noqa: BLE001
                continue
        hub.publish({"type": "error", "message": f"could not reopen {target} after flashing"})

    try:
        job = builder.start(body.sketch, body.board, body.action, port,
                            before=before, after=after if (was_connected or body.port) else None)
    except (ValueError, RuntimeError, FileNotFoundError) as e:
        raise HTTPException(400, str(e))
    return job


@app.get("/api/firmware/build/status")
async def api_build_status() -> dict[str, Any]:
    return {"current": builder.current, "history": builder.history[-10:]}


# ---- Home Assistant ------------------------------------------------------------
class SettingsPatch(BaseModel):
    wifi_ssid: Optional[str] = None
    wifi_pass: Optional[str] = None
    ha_url: Optional[str] = None
    ha_token: Optional[str] = None
    rx_pin: Optional[str] = None
    bridge_host: Optional[str] = None     # where the installed bridge answers, default reremote-bridge.local


class ActionBody(BaseModel):
    domain: str
    service: str
    entity_id: str
    data: dict[str, Any] = {}


@app.get("/api/ha/settings")
async def api_ha_settings() -> dict[str, Any]:
    return _public_settings()


@app.put("/api/ha/settings")
async def api_ha_save_settings(body: SettingsPatch) -> dict[str, Any]:
    values = {k: v for k, v in body.model_dump().items() if v is not None}
    # empty secret means "keep the stored one"
    for k in SECRET_KEYS:
        if values.get(k) == "":
            values.pop(k)
    if "bridge_host" in values:
        values["bridge_host"] = ota.normalize_host(values["bridge_host"])
    db.set_settings(values)
    return _public_settings()


@app.post("/api/ha/test")
async def api_ha_test() -> dict[str, Any]:
    try:
        return await asyncio.to_thread(_ha().ping)
    except RuntimeError as e:
        raise HTTPException(400, str(e))


@app.get("/api/ha/entities")
async def api_ha_entities() -> dict[str, Any]:
    try:
        ents = await asyncio.to_thread(_ha().entities)
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    return {"entities": ents, "actions": ACTIONS}


@app.get("/api/ha/actions")
async def api_ha_actions() -> dict[str, Any]:
    return {"actions": {str(k): v for k, v in db.list_actions().items()}, "catalog": ACTIONS}


@app.put("/api/ha/actions/{button_id}")
async def api_ha_set_action(button_id: int, body: ActionBody) -> dict[str, Any]:
    if not db.get_button(button_id):
        raise HTTPException(404, "button not found")
    if body.domain not in ACTIONS:
        raise HTTPException(400, f"unsupported domain {body.domain}")
    return db.set_action(button_id, body.domain, body.service, body.entity_id, body.data)


@app.delete("/api/ha/actions/{button_id}")
async def api_ha_delete_action(button_id: int) -> dict[str, bool]:
    return {"deleted": db.delete_action(button_id)}


@app.post("/api/ha/actions/{button_id}/run")
async def api_ha_run_action(button_id: int) -> dict[str, Any]:
    act = db.list_actions().get(button_id)
    if not act:
        raise HTTPException(404, "no action mapped to this button")
    path, data = action_to_call(act)
    try:
        result = await asyncio.to_thread(_ha().call, act["domain"], act["service"], data)
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    return {"called": path, "data": data, "changed": len(result or [])}


@app.get("/api/ha/config.h", response_class=PlainTextResponse)
async def api_ha_config() -> str:
    return generate_config_h(_settings(), db.buttons_with_samples(), db.list_actions())


# ---- the installed bridge (over WiFi) -------------------------------------------
_bridge_cache: dict[str, Any] = {"host": None, "until": 0.0, "info": None}
_bridge_probe: Optional[asyncio.Future] = None


async def _probe_bridge(host: str) -> dict[str, Any]:
    """One probe at a time, results cached (3 s online, 20 s offline): a failed
    mDNS lookup takes ~5 s on macOS and two pollers in the UI ask regularly."""
    global _bridge_probe
    if _bridge_cache["host"] == host and _bridge_cache["until"] > time.time():
        return _bridge_cache["info"]
    if _bridge_probe is None or _bridge_probe.done() or _bridge_probe.get_name() != host:
        _bridge_probe = asyncio.ensure_future(asyncio.to_thread(ota.status, host))
        _bridge_probe.set_name(host)
    try:
        info = await asyncio.wait_for(asyncio.shield(_bridge_probe), timeout=2.5)
    except asyncio.TimeoutError:
        return {"online": False, "host": host, "pending": True, "error": "still checking"}
    _bridge_cache.update(host=host, info=info, until=time.time() + (3 if info.get("online") else 20))
    return info


@app.get("/api/bridge/status")
async def api_bridge_status() -> dict[str, Any]:
    """Probe the bridge at bridge_host; {"online": false, "error": ...} when it does not answer."""
    host = _settings()["bridge_host"]
    try:
        info = dict(await _probe_bridge(host))
    except asyncio.CancelledError:
        if not _shutting_down():
            raise
        info = {"online": False, "host": host, "error": "server shutting down"}
    info["ota_port"] = f"{fw.OTA_PREFIX}{info['host']}"
    return info


@app.post("/api/bridge/reboot")
async def api_bridge_reboot() -> dict[str, bool]:
    st = _settings()
    try:
        await asyncio.to_thread(ota.reboot, st["bridge_host"], st["ota_token"])
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    return {"rebooting": True}


# ---- captures / export -------------------------------------------------------
@app.get("/api/captures")
async def api_captures(limit: int = 50) -> list[dict[str, Any]]:
    return db.recent_captures(min(max(limit, 1), 500))


@app.delete("/api/captures")
async def api_clear_captures() -> dict[str, int]:
    return {"deleted": db.clear_captures()}


@app.get("/api/export")
async def api_export() -> JSONResponse:
    return JSONResponse(db.export(), headers={
        "Content-Disposition": 'attachment; filename="remotes-export.json"'})


if __name__ == "__main__":
    import uvicorn

    ap = argparse.ArgumentParser(description="ReRemote server")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--simulate", action="store_true", help="start with the fake Arduino")
    args = ap.parse_args()
    if args.simulate:
        os.environ["REREMOTE_SIMULATE"] = "1"
    # Run the Server object ourselves so the SSE streams can see should_exit and
    # close on Ctrl-C; the grace timeout is a backstop for any other open request.
    _server = uvicorn.Server(uvicorn.Config(app, host=args.host, port=args.port,
                                            timeout_graceful_shutdown=3))
    try:
        _server.run()
    except KeyboardInterrupt:
        pass            # uvicorn re-raises the Ctrl-C after a clean shutdown

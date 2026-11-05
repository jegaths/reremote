"""rc-capture web server: FastAPI + SQLite + pyserial.

Run:  uvicorn app:app --reload          (from the server/ directory)
  or: python app.py [--db path] [--simulate]
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
import logging
import os
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from capture import CaptureEngine, EventHub, list_serial_ports
from db import Database
from decoder import decode
from matcher import match_capture

HERE = Path(__file__).parent
DB_PATH = os.environ.get("RC_DB", str(HERE.parent / "data" / "remotes.sqlite"))
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


@asynccontextmanager
async def lifespan(_app: FastAPI):
    hub.loop = asyncio.get_running_loop()
    if os.environ.get("RC_SIMULATE") == "1":
        engine.start_simulator()
    yield
    engine.disconnect()


app = FastAPI(title="rc-capture", version="1.0.0", lifespan=lifespan)


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
    return list_serial_ports()


@app.get("/api/status")
async def api_status() -> dict[str, Any]:
    return engine.status()


@app.post("/api/connect")
async def api_connect(body: ConnectBody) -> dict[str, Any]:
    try:
        await asyncio.to_thread(engine.connect, body.port, body.baud)
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
        try:
            yield EventHub.format_sse({"type": "status", **engine.status()})
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15)
                    yield EventHub.format_sse(ev)
                except asyncio.TimeoutError:
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

    ap = argparse.ArgumentParser(description="rc-capture server")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--simulate", action="store_true", help="start with the fake Arduino")
    args = ap.parse_args()
    if args.simulate:
        os.environ["RC_SIMULATE"] = "1"
    uvicorn.run(app, host=args.host, port=args.port)

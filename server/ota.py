"""Over-the-air updates for the ha_bridge firmware.

The bridge runs a small HTTP server (see firmware/ha_bridge/ha_bridge.ino):
    GET  http://<host>/status   JSON with version, ip, rssi, uptime ...
    POST http://<host>/update   multipart upload of the new .bin, X-OTA-Token header
    POST http://<host>/reboot   X-OTA-Token header
<host> defaults to reremote-bridge.local, which macOS resolves through Bonjour.
"""
from __future__ import annotations

import json
import secrets
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Optional

DEFAULT_HOST = "reremote-bridge.local"
Emit = Callable[..., None]


def normalize_host(host: Optional[str]) -> str:
    h = (host or "").strip()
    for prefix in ("http://", "https://", "ota:"):
        if h.lower().startswith(prefix):
            h = h[len(prefix):]
    return h.strip("/") or DEFAULT_HOST


def hostname_for(host: Optional[str]) -> str:
    """mDNS name the firmware should announce for this bridge address."""
    h = normalize_host(host).split(":")[0]
    if h.endswith(".local"):
        return h[:-len(".local")]
    return "reremote-bridge"


def new_token() -> str:
    return secrets.token_urlsafe(18)


def _request(host: str, path: str, data: Optional[bytes] = None, headers: Optional[dict[str, str]] = None,
             timeout: float = 3.0) -> tuple[int, str]:
    req = urllib.request.Request(f"http://{normalize_host(host)}{path}", data=data,
                                 method="POST" if data is not None else "GET", headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read().decode(errors="replace")


def status(host: Optional[str], timeout: float = 2.5) -> dict[str, Any]:
    """Probe the bridge. Never raises: {"online": False, "error": ...} when unreachable."""
    host = normalize_host(host)
    t0 = time.time()
    try:
        code, text = _request(host, "/status", timeout=timeout)
        info = json.loads(text)
        if not isinstance(info, dict) or info.get("firmware") != "ha_bridge":
            return {"online": False, "host": host, "error": "something answered, but not ha_bridge"}
        return {"online": True, "host": host, "latency_ms": int((time.time() - t0) * 1000), **info}
    except urllib.error.HTTPError as e:
        return {"online": False, "host": host, "error": f"HTTP {e.code}"}
    except (urllib.error.URLError, socket.timeout, TimeoutError, OSError, ValueError) as e:
        return {"online": False, "host": host, "error": friendly_error(e, host)}


def friendly_error(e: BaseException, host: str) -> str:
    reason = getattr(e, "reason", e)
    msg = str(reason)
    errno = getattr(reason, "errno", None)
    if errno == 8 or "nodename nor servname" in msg or "Name or service not known" in msg:
        return f"{host} does not resolve: the bridge is off, or runs a firmware older than 1.1.0 (flash once over USB)"
    if errno == 61 or "refused" in msg:
        return "connection refused: something is at that address but it is not the bridge firmware 1.1.0+"
    if errno == 65 or "No route to host" in msg:
        return "no route to host: start the server from your own Terminal and allow Local Network access (see README)"
    if "timed out" in msg or isinstance(reason, (socket.timeout, TimeoutError)):
        return "no answer (timed out)"
    return msg


def multipart(filename: str, data: bytes, field: str = "firmware") -> tuple[bytes, str]:
    boundary = "----reremote" + secrets.token_hex(12)
    head = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"; filename=\"{filename}\"\r\n"
            "Content-Type: application/octet-stream\r\n\r\n").encode()
    tail = f"\r\n--{boundary}--\r\n".encode()
    return head + data + tail, f"multipart/form-data; boundary={boundary}"


def upload(host: Optional[str], token: str, bin_path: Path, emit: Emit, timeout: float = 180.0) -> dict[str, Any]:
    """Push bin_path to the bridge and wait for it to come back. Raises RuntimeError on failure."""
    host = normalize_host(host)
    if not token:
        raise RuntimeError("no OTA token configured")
    data = Path(bin_path).read_bytes()
    emit(f"$ ota upload {bin_path.name} -> http://{host}/update ({len(data)} bytes)")
    before = status(host, timeout=4)
    if not before.get("online"):
        raise RuntimeError(f"bridge {host} is not reachable: {before.get('error')}")
    if not before.get("ota"):
        raise RuntimeError("the firmware on the bridge has no OTA token; flash it once over USB")
    emit(f"bridge answered: ha_bridge {before.get('version')} at {before.get('ip')}, "
         f"rssi {before.get('rssi')} dBm, up {before.get('uptime_s')} s")
    body, ctype = multipart(bin_path.name, data)
    t0 = time.time()
    try:
        code, text = _request(host, "/update", data=body, timeout=timeout,
                              headers={"Content-Type": ctype, "X-OTA-Token": token})
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:200]
        if e.code == 401:
            raise RuntimeError("the bridge rejected the OTA token; flash once over USB so both sides share the new token") from e
        raise RuntimeError(f"bridge replied {e.code}: {detail}") from e
    except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as e:
        # the bridge closes the connection right after replying; an early EOF is normal
        if time.time() - t0 < 2:
            raise RuntimeError(f"upload failed: {getattr(e, 'reason', e)}") from e
        emit(f"connection closed by the bridge after {time.time() - t0:.1f} s; checking whether it rebooted")
        code, text = 200, "(no reply)"
    emit(f"bridge: {code} {text.strip()} after {time.time() - t0:.1f} s")
    if code != 200:
        raise RuntimeError(f"update refused: {text.strip()}")
    return wait_for_reboot(host, emit)


def wait_for_reboot(host: str, emit: Emit, timeout: float = 45.0) -> dict[str, Any]:
    emit("waiting for the bridge to reboot ...")
    deadline = time.time() + timeout
    time.sleep(2.0)
    while time.time() < deadline:
        st = status(host, timeout=2)
        if st.get("online") and (st.get("uptime_s") or 0) < 60:
            emit(f"bridge is back: ha_bridge {st.get('version')} at {st.get('ip')}, up {st.get('uptime_s')} s", level="ok")
            return st
        time.sleep(1.0)
    raise RuntimeError("the bridge did not come back within 45 s; check its power or the serial log")


def reboot(host: Optional[str], token: str) -> None:
    try:
        _request(host, "/reboot", data=b"", headers={"X-OTA-Token": token}, timeout=5)
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise RuntimeError("the bridge rejected the OTA token") from e
        raise RuntimeError(f"bridge replied {e.code}") from e
    except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as e:
        raise RuntimeError(f"cannot reach {normalize_host(host)}: {getattr(e, 'reason', e)}") from e

"""Serial link to the Arduino and grouping of repeated frames into captures.

A remote transmits the same frame many times while a button is held. The
firmware reports every frame; here consecutive frames that match within jitter
are collected into a *burst*. When the burst goes quiet for `settle_ms` and has
at least `min_repeats` members, it becomes a Capture event that the web UI
shows and the database logs. Single unrepeated frames are almost always
receiver noise and are dropped.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import serial
import serial.tools.list_ports

from decoder import average_frames, decode, frames_match

log = logging.getLogger("capture")


# USB vendor/product ids of the boards we expect, for a friendlier port label
KNOWN_USB = {
    (0x303A, 0x1001): "ESP32 native USB (S3/C3/C6)",
    (0x303A, 0x0002): "ESP32-S2/S3 native USB (TinyUSB)",
    (0x10C4, 0xEA60): "CP2102 UART bridge (ESP32 dev board)",
    (0x1A86, 0x55D3): "CH343 UART bridge (ESP32 dev board)",
    (0x1A86, 0x55D4): "CH9102 UART bridge (ESP32 dev board)",
    (0x1A86, 0x7523): "CH340 (Arduino Nano clone)",
    (0x0403, 0x6001): "FTDI (Arduino Nano)",
    (0x2341, None): "Arduino",
}


def _label(p) -> str:
    if p.vid is not None:
        for (vid, pid), name in KNOWN_USB.items():
            if vid == p.vid and (pid is None or pid == p.pid):
                return name
    return p.description or ""


def list_serial_ports() -> list[dict[str, str]]:
    ports = []
    for p in serial.tools.list_ports.comports():
        dev = p.device
        if dev.startswith("/dev/cu.Bluetooth") or dev.endswith("debug-console"):
            continue                       # macOS built-ins, never a board
        ports.append({"device": dev, "description": _label(p), "hwid": p.hwid or ""})
    # real USB devices first
    ports.sort(key=lambda x: (0 if "usb" in x["device"].lower() or x["hwid"].startswith("USB") else 1,
                              x["device"]))
    return ports


def parse_frame_line(line: str) -> Optional[list[int]]:
    """'F <millis> <n> <d1> ... <dn>' -> [d1..dn]"""
    parts = line.strip().split()
    if len(parts) < 4 or parts[0] != "F":
        return None
    try:
        n = int(parts[2])
        vals = [int(x) for x in parts[3:]]
    except ValueError:
        return None
    if n != len(vals) or n < 4:
        return None
    return vals


@dataclass
class Burst:
    frames: list[list[int]] = field(default_factory=list)
    first_at: float = 0.0
    last_at: float = 0.0


class CaptureEngine:
    """Owns the serial thread and emits events through a callback."""

    def __init__(self, on_event: Callable[[dict[str, Any]], None],
                 min_repeats: int = 2, settle_ms: int = 350):
        self.on_event = on_event
        self.min_repeats = min_repeats
        self.settle_ms = settle_ms
        self._ser: Optional[serial.Serial] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._burst: Optional[Burst] = None
        self.port: Optional[str] = None
        self.firmware: Optional[str] = None
        self.board: Optional[str] = None
        self.frames_seen = 0
        self.frames_dropped = 0
        self.last_frame_at: Optional[float] = None
        self.source = "none"
        self.log: deque[dict[str, Any]] = deque(maxlen=2000)
        self._log_seq = 0

    def add_log(self, line: str, kind: str = "other") -> None:
        """Record a raw line (from the board or the server) and broadcast it."""
        self._log_seq += 1
        entry = {"seq": self._log_seq, "t": time.time(), "kind": kind, "line": line[:400]}
        self.log.append(entry)
        self.on_event({"type": "log", **entry})

    # ---- lifecycle ---------------------------------------------------------
    def status(self) -> dict[str, Any]:
        return {
            "connected": self._thread is not None and self._thread.is_alive(),
            "source": self.source,
            "port": self.port,
            "firmware": self.firmware,
            "board": self.board,
            "frames_seen": self.frames_seen,
            "frames_dropped": self.frames_dropped,
            "last_frame_at": self.last_frame_at,
            "min_repeats": self.min_repeats,
            "settle_ms": self.settle_ms,
        }

    def connect(self, port: str, baud: int = 115200) -> None:
        self.disconnect()
        # Open with DTR/RTS low. Asserting them on open resets an ESP32-S3's
        # native USB port (and can drop it into download mode); the Nano does
        # not need the auto-reset either since the firmware answers "?".
        ser = serial.Serial()
        ser.port, ser.baudrate, ser.timeout = port, baud, 0.2
        ser.dtr = False
        ser.rts = False
        ser.open()
        self._ser = ser
        self.add_log(f"[server] opened {port} @ {baud}", "server")
        self.port = port
        self.source = "serial"
        self._stop.clear()
        self._thread = threading.Thread(target=self._serial_loop, name="serial", daemon=True)
        self._thread.start()
        # ask the board to identify itself
        try:
            time.sleep(0.5)
            ser.write(b"?\n")
        except serial.SerialException:
            pass
        self.on_event({"type": "status", **self.status()})

    def start_simulator(self) -> None:
        from simulator import simulated_lines
        self.disconnect()
        self.port = "simulator"
        self.source = "simulator"
        self._stop.clear()
        self._thread = threading.Thread(target=self._line_loop, args=(simulated_lines(self._stop),),
                                        name="simulator", daemon=True)
        self._thread.start()
        self.on_event({"type": "status", **self.status()})

    def disconnect(self) -> None:
        if self._thread and self._thread.is_alive():
            self.add_log(f"[server] closing {self.port}", "server")
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
        self._thread = None
        if self._ser:
            try:
                self._ser.close()
            except Exception:  # noqa: BLE001
                pass
        self._ser = None
        self.port = None
        self.firmware = None
        self.board = None
        self.source = "none"
        self._burst = None
        self.on_event({"type": "status", **self.status()})

    def send_command(self, cmd: str) -> bool:
        if self._ser and self._ser.is_open:
            self._ser.write((cmd.strip() + "\n").encode())
            self.add_log(f"> {cmd.strip()}", "server")
            return True
        return False

    # ---- threads -----------------------------------------------------------
    def _serial_loop(self) -> None:
        assert self._ser is not None
        buf = b""
        while not self._stop.is_set():
            try:
                chunk = self._ser.read(256)
            except (serial.SerialException, OSError) as e:
                log.warning("serial error: %s", e)
                self.add_log(f"[server] serial error: {e}", "server")
                self.on_event({"type": "error", "message": f"serial error: {e}"})
                break
            if chunk:
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    self._handle_line(line.decode("ascii", "replace"))
            self._flush_burst_if_quiet()
        try:
            self._ser.close()
        except Exception:  # noqa: BLE001
            pass
        self._ser = None
        self._thread = None
        self.source = "none"
        self.on_event({"type": "status", **self.status()})

    def _line_loop(self, lines) -> None:
        for line in lines:
            if self._stop.is_set():
                break
            if line is not None:
                self._handle_line(line)
            self._flush_burst_if_quiet()

    # ---- frame handling ----------------------------------------------------
    def _handle_line(self, line: str) -> None:
        line = line.strip("\r\n")
        if not line.strip():
            return
        kind = ("frame" if line.startswith("F ") else "ident" if line.startswith("I ")
                else "error" if line.startswith("E ") else "other")
        self.add_log(line, kind)
        line = line.strip()
        if line.startswith("I "):
            self.firmware = line[2:]
            for tok in line.split():
                if tok.startswith("board="):
                    self.board = tok[6:]
            self.on_event({"type": "status", **self.status()})
            return
        if line.startswith("E "):
            self.on_event({"type": "error", "message": line[2:]})
            return
        frame = parse_frame_line(line)
        if frame is None:
            return
        self.frames_seen += 1
        self.last_frame_at = time.time()
        self.on_event({"type": "frame", "n": len(frame), "frames_seen": self.frames_seen})
        with self._lock:
            now = time.time()
            b = self._burst
            if b and frames_match(b.frames[-1], frame):
                b.frames.append(frame)
                b.last_at = now
                return
            # different frame: finish the previous burst, start a new one
            if b:
                self._finish_burst(b)
            self._burst = Burst(frames=[frame], first_at=now, last_at=now)

    def _flush_burst_if_quiet(self) -> None:
        with self._lock:
            b = self._burst
            if b and (time.time() - b.last_at) * 1000 >= self.settle_ms:
                self._burst = None
                self._finish_burst(b)

    def _finish_burst(self, b: Burst) -> None:
        if len(b.frames) < self.min_repeats:
            self.frames_dropped += len(b.frames)
            return
        timings = average_frames(b.frames)
        d = decode(timings)
        self.on_event({
            "type": "capture",
            "timings": timings,
            "repeats": len(b.frames),
            "duration_ms": int((b.last_at - b.first_at) * 1000),
            **d.to_dict(),
        })


class EventHub:
    """Fan-out of engine events to SSE subscribers on the asyncio loop."""

    def __init__(self) -> None:
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self._queues: set[asyncio.Queue] = set()

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._queues.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._queues.discard(q)

    def publish(self, event: dict[str, Any]) -> None:
        """Thread-safe: may be called from the serial thread."""
        if self.loop is None:
            return
        self.loop.call_soon_threadsafe(self._publish, event)

    def _publish(self, event: dict[str, Any]) -> None:
        for q in list(self._queues):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

    @staticmethod
    def format_sse(event: dict[str, Any]) -> str:
        return f"event: {event.get('type', 'message')}\ndata: {json.dumps(event)}\n\n"

"""Sketch management and arduino-cli build/flash runner.

Sketches are folders under firmware/ that contain a .ino of the same name and
optionally a sketch.json manifest:
    {"title", "description", "boards": [...], "libraries": [...],
     "config": "<generator name>", "generated": ["config.h"]}
Files of a sketch can be read and written from the UI (text files only, inside
the sketch folder). Builds run arduino-cli in a thread and stream every output
line as a "build" event through the callback.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

FIRMWARE_DIR = Path(__file__).resolve().parents[1] / "firmware"

FQBN = {
    "esp32s3": "esp32:esp32:esp32s3:CDCOnBoot=cdc",
    "nano": "arduino:avr:nano",
    "nano-old": "arduino:avr:nano:cpu=atmega328old",
}
BOARD_LABEL = {"esp32s3": "ESP32-S3", "nano": "Arduino Nano", "nano-old": "Arduino Nano (old bootloader)"}

TEXT_EXT = {".ino", ".h", ".hpp", ".c", ".cpp", ".json", ".md", ".txt", ".example"}
NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,40}$")

NEW_SKETCH_TEMPLATE = '''/*
 * {name}.ino
 */

void setup() {{
  Serial.begin(115200);
  Serial.println("I {name} 0.1.0");
}}

void loop() {{
}}
'''


def guess_board(description: str) -> str:
    d = (description or "").lower()
    if "esp32" in d:
        return "esp32s3"
    if "ch340" in d:
        return "nano-old"        # CH340 clones nearly always ship the old bootloader
    if "ftdi" in d or "arduino" in d:
        return "nano"
    return "unknown"


def arduino_cli() -> Optional[str]:
    return shutil.which("arduino-cli")


# ---- sketches -----------------------------------------------------------------
def _manifest(folder: Path) -> dict[str, Any]:
    mf = folder / "sketch.json"
    if mf.exists():
        try:
            return json.loads(mf.read_text())
        except json.JSONDecodeError:
            pass
    return {}


def list_sketches() -> list[dict[str, Any]]:
    out = []
    for folder in sorted(p for p in FIRMWARE_DIR.iterdir() if p.is_dir()):
        ino = folder / f"{folder.name}.ino"
        if not ino.exists():
            continue
        m = _manifest(folder)
        out.append({
            "name": folder.name,
            "title": m.get("title", folder.name),
            "description": m.get("description", ""),
            "boards": m.get("boards", list(FQBN)),
            "libraries": m.get("libraries", []),
            "config": m.get("config"),
            "generated": m.get("generated", []),
            "files": sketch_files(folder.name),
            "modified": int(max(f.stat().st_mtime for f in folder.iterdir() if f.is_file())),
        })
    return out


def sketch_dir(name: str) -> Path:
    if not NAME_RE.match(name):
        raise ValueError("invalid sketch name")
    folder = (FIRMWARE_DIR / name).resolve()
    if folder.parent != FIRMWARE_DIR.resolve():
        raise ValueError("invalid sketch name")
    return folder


def sketch_files(name: str) -> list[str]:
    folder = sketch_dir(name)
    if not folder.exists():
        return []
    files = [f.name for f in folder.iterdir()
             if f.is_file() and (f.suffix in TEXT_EXT or f.name.endswith(".example.h"))]
    main = f"{name}.ino"
    return sorted(files, key=lambda f: (f != main, f))


def _file_path(name: str, filename: str) -> Path:
    folder = sketch_dir(name)
    if "/" in filename or "\\" in filename or filename.startswith(".") or not filename:
        raise ValueError("invalid file name")
    p = (folder / filename).resolve()
    if p.parent != folder:
        raise ValueError("invalid file name")
    if p.suffix not in TEXT_EXT and not p.name.endswith(".example.h"):
        raise ValueError("only source and text files can be edited")
    return p


def read_file(name: str, filename: str) -> str:
    p = _file_path(name, filename)
    if not p.exists():
        raise FileNotFoundError(filename)
    return p.read_text()


def write_file(name: str, filename: str, content: str) -> None:
    p = _file_path(name, filename)
    if not p.parent.exists():
        raise FileNotFoundError(name)
    p.write_text(content)


def delete_file(name: str, filename: str) -> None:
    p = _file_path(name, filename)
    if p.name == f"{name}.ino":
        raise ValueError("the main .ino cannot be deleted; delete the sketch instead")
    p.unlink(missing_ok=True)


def create_sketch(name: str, title: str = "", description: str = "",
                  copy_from: Optional[str] = None) -> dict[str, Any]:
    folder = sketch_dir(name)
    if folder.exists():
        raise FileExistsError(name)
    folder.mkdir(parents=True)
    if copy_from:
        src = sketch_dir(copy_from)
        for f in src.iterdir():
            if f.is_file() and f.name != "config.h":
                target = folder / (f"{name}.ino" if f.name == f"{copy_from}.ino" else f.name)
                shutil.copy(f, target)
        manifest = _manifest(src)
    else:
        (folder / f"{name}.ino").write_text(NEW_SKETCH_TEMPLATE.format(name=name))
        manifest = {"boards": list(FQBN), "libraries": []}
    manifest["title"] = title or name
    manifest["description"] = description
    (folder / "sketch.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return next(s for s in list_sketches() if s["name"] == name)


def delete_sketch(name: str) -> None:
    folder = sketch_dir(name)
    if name in ("rf_capture", "ha_bridge"):
        raise ValueError("the built-in sketches cannot be deleted")
    shutil.rmtree(folder, ignore_errors=True)


# ---- builds -----------------------------------------------------------------
class BuildRunner:
    """One arduino-cli job at a time; lines go to on_event as build events."""

    def __init__(self, on_event: Callable[[dict[str, Any]], None]):
        self.on_event = on_event
        self._lock = threading.Lock()
        self._proc: Optional[subprocess.Popen] = None
        self.current: Optional[dict[str, Any]] = None
        self.history: list[dict[str, Any]] = []

    def busy(self) -> bool:
        return self.current is not None

    def _emit(self, line: str, **extra: Any) -> None:
        ev = {"type": "build", "line": line, "t": time.time(), **extra}
        self.on_event(ev)

    def start(self, sketch: str, board: str, action: str, port: Optional[str] = None,
              before: Optional[Callable[[], None]] = None,
              after: Optional[Callable[[bool], None]] = None) -> dict[str, Any]:
        if action not in ("compile", "flash"):
            raise ValueError("action must be compile or flash")
        if board not in FQBN:
            raise ValueError(f"unknown board {board}")
        if action == "flash" and not port:
            raise ValueError("port required to flash")
        if not arduino_cli():
            raise RuntimeError("arduino-cli not found; run 'make setup'")
        folder = sketch_dir(sketch)
        if not (folder / f"{sketch}.ino").exists():
            raise FileNotFoundError(sketch)
        with self._lock:
            if self.current:
                raise RuntimeError("a build is already running")
            self.current = {"sketch": sketch, "board": board, "action": action, "port": port,
                            "started": time.time()}
        threading.Thread(target=self._run, args=(folder, board, action, port, before, after),
                         name="build", daemon=True).start()
        return self.current

    def _run(self, folder: Path, board: str, action: str, port: Optional[str],
             before: Optional[Callable[[], None]], after: Optional[Callable[[bool], None]]) -> None:
        ok = False
        try:
            libs = _manifest(folder).get("libraries", [])
            if libs:
                self._emit(f"$ arduino-cli lib install {' '.join(libs)}")
                self._stream(["arduino-cli", "lib", "install", *libs])
            if before:
                before()
            cmd = ["arduino-cli", "compile", "--fqbn", FQBN[board], str(folder)]
            self._emit("$ " + " ".join(cmd))
            rc = self._stream(cmd)
            if rc != 0:
                self._emit(f"compile failed (exit {rc})", level="error")
                return
            if action == "flash":
                cmd = ["arduino-cli", "upload", "--fqbn", FQBN[board], "-p", port, str(folder)]
                self._emit("$ " + " ".join(cmd))
                rc = self._stream(cmd)
                if rc != 0:
                    self._emit(f"upload failed (exit {rc})", level="error")
                    return
            ok = True
            self._emit("compile OK" if action == "compile" else "flash OK", level="ok")
        except Exception as e:  # noqa: BLE001
            self._emit(f"error: {e}", level="error")
        finally:
            if after:
                try:
                    after(ok)
                except Exception as e:  # noqa: BLE001
                    self._emit(f"post-flash error: {e}", level="error")
            with self._lock:
                job = self.current or {}
                job["ok"] = ok
                job["finished"] = time.time()
                self.history.append(job)
                self.current = None
            self._emit("", done=True, ok=ok)

    def _stream(self, cmd: list[str]) -> int:
        env = {**os.environ, "PYTHONUNBUFFERED": "1", "TERM": "dumb"}
        self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                      text=True, bufsize=1, env=env, cwd=str(FIRMWARE_DIR.parent))
        assert self._proc.stdout is not None
        for raw in self._proc.stdout:
            for line in raw.replace("\r", "\n").split("\n"):
                line = _strip_ansi(line).rstrip()
                if line:
                    self._emit(line)
        self._proc.wait()
        rc = self._proc.returncode
        self._proc = None
        return rc


_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _strip_ansi(s: str) -> str:
    return _ANSI.sub("", s)

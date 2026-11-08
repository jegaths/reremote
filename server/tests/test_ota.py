import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

import firmware as fw  # noqa: E402
import ota  # noqa: E402
from ha import generate_config_h  # noqa: E402


def test_host_helpers():
    assert ota.normalize_host(None) == "reremote-bridge.local"
    assert ota.normalize_host(" http://192.168.88.6/ ") == "192.168.88.6"
    assert ota.normalize_host("ota:fan-bridge.local") == "fan-bridge.local"
    assert ota.hostname_for("fan-bridge.local") == "fan-bridge"
    assert ota.hostname_for("192.168.88.6:80") == "reremote-bridge"
    assert fw.is_ota_port("ota:reremote-bridge.local") and not fw.is_ota_port("/dev/cu.usbmodem1") and not fw.is_ota_port(None)
    assert fw.ota_host("ota:reremote-bridge.local") == "reremote-bridge.local"
    assert len(ota.new_token()) >= 20


def test_multipart_body():
    body, ctype = ota.multipart("fw.bin", b"\x00\x01binary")
    boundary = ctype.split("boundary=")[1]
    assert body.startswith(f"--{boundary}\r\n".encode()) and body.endswith(f"\r\n--{boundary}--\r\n".encode())
    assert b'filename="fw.bin"' in body and b"\x00\x01binary" in body


def test_config_h_has_ota_defines():
    text = generate_config_h({"bridge_host": "fan-bridge.local", "ota_token": "s3cret"}, [], {})
    assert '#define HOSTNAME  "fan-bridge"' in text and '#define OTA_TOKEN "s3cret"' in text


def test_status_offline_is_clean():
    st = ota.status("127.0.0.1:1", timeout=1)
    assert st["online"] is False and st["host"] == "127.0.0.1:1" and st["error"]


class _Bridge(BaseHTTPRequestHandler):
    """Fake ha_bridge: answers /status, accepts /update with the right token."""
    token = "tok"
    uploads: list[bytes] = []
    uptime = 500

    def log_message(self, *_):  # quiet
        pass

    def do_GET(self):
        if self.path == "/status":
            body = (b'{"firmware":"ha_bridge","version":"1.1.0","ip":"10.0.0.2","rssi":-60,'
                    b'"uptime_s":%d,"buttons":11,"presses":3,"last_button":"On","last_press_ago_s":5,'
                    b'"free_heap":200000,"sketch_size":1,"sketch_md5":"x","ota":true}' % type(self).uptime)
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(body)
        else:
            self.send_response(404); self.end_headers()

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        data = self.rfile.read(n)
        if self.headers.get("X-OTA-Token") != type(self).token:
            self.send_response(401); self.end_headers(); self.wfile.write(b"bad token"); return
        if self.path == "/update":
            type(self).uploads.append(data)
            type(self).uptime = 3          # "rebooted"
            self.send_response(200); self.end_headers(); self.wfile.write(b"OK, rebooting")
        elif self.path == "/reboot":
            self.send_response(200); self.end_headers(); self.wfile.write(b"rebooting")
        else:
            self.send_response(404); self.end_headers()


@pytest.fixture
def fake_bridge():
    srv = HTTPServer(("127.0.0.1", 0), _Bridge)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    _Bridge.uploads.clear(); _Bridge.uptime = 500
    yield f"127.0.0.1:{srv.server_port}"
    srv.shutdown()


def test_upload_roundtrip(fake_bridge, tmp_path, monkeypatch):
    monkeypatch.setattr(ota.time, "sleep", lambda *_: None)
    binf = tmp_path / "ha_bridge.ino.bin"; binf.write_bytes(b"\xe9" + b"\x00" * 100)
    lines = []
    st = ota.upload(fake_bridge, "tok", binf, lambda line, **kw: lines.append(line))
    assert st["online"] and st["uptime_s"] == 3
    assert len(_Bridge.uploads) == 1 and b"\xe9" + b"\x00" * 100 in _Bridge.uploads[0]
    assert any(l.startswith("$ ota upload") for l in lines) and any("bridge is back" in l for l in lines)
    with pytest.raises(RuntimeError, match="rejected the OTA token"):
        ota.upload(fake_bridge, "wrong", binf, lambda *a, **k: None)
    ota.reboot(fake_bridge, "tok")
    with pytest.raises(RuntimeError):
        ota.reboot(fake_bridge, "nope")


def test_bridge_api(tmp_path, monkeypatch, fake_bridge):
    monkeypatch.setenv("REREMOTE_DB", str(tmp_path / "api.sqlite"))
    sys.modules.pop("app", None)
    import app as appmod
    from fastapi.testclient import TestClient

    with TestClient(appmod.app) as c:
        s = c.get("/api/ha/settings").json()
        assert s["bridge_host"] == "reremote-bridge.local" and s["ota_token_set"] and "ota_token" not in s
        assert c.put("/api/ha/settings", json={"bridge_host": f"http://{fake_bridge}/"}).json()["bridge_host"] == fake_bridge
        token = appmod._settings()["ota_token"]
        _Bridge.token = token
        st = c.get("/api/bridge/status").json()
        assert st["online"] and st["version"] == "1.1.0" and st["ota_port"] == f"ota:{fake_bridge}"
        assert '#define OTA_TOKEN "%s"' % token in c.get("/api/ha/config.h").text
        assert c.post("/api/bridge/reboot").status_code == 200
        # OTA target is ESP32-S3 and ha_bridge only
        r = c.post("/api/firmware/build", json={"sketch": "ha_bridge", "board": "nano", "action": "flash", "port": f"ota:{fake_bridge}"})
        assert r.status_code == 400 and "ESP32-S3" in r.json()["detail"]
        r = c.post("/api/firmware/build", json={"sketch": "rf_capture", "board": "esp32s3", "action": "flash", "port": f"ota:{fake_bridge}"})
        assert r.status_code == 400 and "no over-the-air" in r.json()["detail"]

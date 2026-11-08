import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

import firmware as fw  # noqa: E402
from decoder import encode_rcswitch  # noqa: E402
from ha import generate_config_h, action_to_call  # noqa: E402


def test_guess_board():
    assert fw.guess_board("ESP32-S3 native USB") == "esp32s3"
    assert fw.guess_board("CP2102 UART bridge (ESP32 dev board)") == "esp32s3"
    assert fw.guess_board("CH340 (Arduino Nano clone)") == "nano-old"
    assert fw.guess_board("FTDI (Arduino Nano)") == "nano"
    assert fw.guess_board("") == "unknown"


def test_builtin_sketches_listed():
    names = {s["name"] for s in fw.list_sketches()}
    assert {"rf_capture", "ha_bridge"} <= names
    bridge = next(s for s in fw.list_sketches() if s["name"] == "ha_bridge")
    assert bridge["config"] == "ha_bridge" and "config.h" in bridge["generated"]
    assert bridge["files"][0] == "ha_bridge.ino"


def test_path_safety():
    with pytest.raises(ValueError):
        fw.sketch_dir("../etc")
    with pytest.raises(ValueError):
        fw.read_file("rf_capture", "../ha_bridge/ha_bridge.ino")
    with pytest.raises(ValueError):
        fw.write_file("rf_capture", "x.bin", "")


def test_config_h_generation():
    buttons = [
        {"id": 1, "name": "On", "remote_id": 1, "remote_name": "Fan", "protocol": "rcswitch-1", "bit_length": 32,
         "code": 0x11C0E69, "fingerprint": "f1", "samples": [{"code": 0x11C0E69}, {"code": 0x11C0E1E}], "sample_count": 2},
        {"id": 2, "name": "Off", "remote_id": 1, "remote_name": "Fan", "protocol": "rcswitch-1", "bit_length": 32,
         "code": 0x11C0678, "fingerprint": "f2", "samples": [{"code": 0x11C0678}], "sample_count": 1},
        {"id": 3, "name": "Weird", "remote_id": 1, "remote_name": "Fan", "protocol": "raw", "bit_length": None,
         "code": None, "fingerprint": "raw-x", "samples": [], "sample_count": 0},
    ]
    actions = {1: {"domain": "fan", "service": "set_percentage", "entity_id": "fan.bed", "data": {"percentage": 66}}}
    text = generate_config_h({"wifi_ssid": 'my "net"', "wifi_pass": "p\\w", "ha_url": "http://ha:8123/", "ha_token": "tok"},
                             buttons, actions)
    assert '#define WIFI_SSID "my \\"net\\""' in text
    assert '#define WIFI_PASS "p\\\\w"' in text
    assert '#define HA_URL    "http://ha:8123"' in text
    assert '{1, 32, 0x011C0E69, 0xFFFFFF00, "Fan / On", "/api/services/fan/set_percentage", "{\\"entity_id\\":\\"fan.bed\\",\\"percentage\\":66}"}' in text
    assert '{1, 32, 0x011C0678, 0xFFFFFF00, "Fan / Off", "", "{}"}' in text   # inherits the mask, no action
    assert "Weird" not in text                                                 # raw buttons cannot go on the board
    assert action_to_call(actions[1]) == ("/api/services/fan/set_percentage", {"entity_id": "fan.bed", "percentage": 66})


def test_http_firmware_and_ha_api(tmp_path, monkeypatch):
    monkeypatch.setenv("REREMOTE_DB", str(tmp_path / "api.sqlite"))
    sys.modules.pop("app", None)
    import app as appmod
    from fastapi.testclient import TestClient

    monkeypatch.setattr(fw, "FIRMWARE_DIR", tmp_path / "fw")
    (tmp_path / "fw").mkdir()
    with TestClient(appmod.app) as c:
        assert c.get("/api/firmware").json()["sketches"] == []
        r = c.post("/api/firmware", json={"name": "blinky", "title": "Blink"})
        assert r.status_code == 201 and r.json()["files"] == ["blinky.ino", "sketch.json"]
        assert c.post("/api/firmware", json={"name": "blinky"}).status_code == 409
        assert c.post("/api/firmware", json={"name": "../x"}).status_code == 400
        assert "void setup()" in c.get("/api/firmware/blinky/files/blinky.ino").text
        r = c.put("/api/firmware/blinky/files/util.h", json={"content": "// hi\n"})
        assert r.status_code == 200 and "util.h" in r.json()["files"]
        assert c.get("/api/firmware/blinky/files/util.h").text == "// hi\n"
        assert c.delete("/api/firmware/blinky/files/blinky.ino").status_code == 400
        assert c.delete("/api/firmware/blinky/files/util.h").status_code == 200
        assert c.post("/api/firmware/build", json={"sketch": "blinky", "board": "esp32s3", "action": "flash"}).status_code == 400  # no port
        assert c.post("/api/firmware/build", json={"sketch": "nope", "board": "esp32s3", "action": "compile"}).status_code == 400
        assert c.delete("/api/firmware/blinky").status_code == 200

        # HA settings: secrets never come back, blanks keep stored values
        r = c.put("/api/ha/settings", json={"wifi_ssid": "net", "wifi_pass": "pw", "ha_url": "http://ha:8123", "ha_token": "tok"}).json()
        assert r["wifi_ssid"] == "net" and r["ha_token_set"] and "ha_token" not in r
        r = c.put("/api/ha/settings", json={"ha_token": ""}).json()
        assert r["ha_token_set"]
        # actions need a button
        rem = c.post("/api/remotes", json={"name": "Fan"}).json()
        btn = c.post("/api/buttons", json={"remote_id": rem["id"], "name": "On", "timings": encode_rcswitch(1, 0x11C0E69, 32, 266)}).json()
        assert c.put(f"/api/ha/actions/{btn['id']}", json={"domain": "fan", "service": "turn_on", "entity_id": "fan.bed"}).status_code == 200
        assert c.put(f"/api/ha/actions/{btn['id']}", json={"domain": "nope", "service": "x", "entity_id": "y"}).status_code == 400
        cfg = c.get("/api/ha/config.h").text
        assert '"/api/services/fan/turn_on"' in cfg and '#define HA_TOKEN  "tok"' in cfg
        assert c.get("/api/ha/actions").json()["actions"][str(btn["id"])]["entity_id"] == "fan.bed"
        # unreachable HA gives a clean 400, not a 500
        assert c.post("/api/ha/test").status_code == 400
        assert c.post(f"/api/ha/actions/{btn['id']}/run").status_code == 400

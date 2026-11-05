import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from capture import CaptureEngine, parse_frame_line  # noqa: E402
from db import Database  # noqa: E402
from decoder import (RCSWITCH_PROTOCOLS, decode, encode_rcswitch, frames_match,  # noqa: E402
                     raw_fingerprint)
from simulator import RAW_PATTERN, _jitter  # noqa: E402


@pytest.mark.parametrize("pid", [p[0] for p in RCSWITCH_PROTOCOLS])
def test_roundtrip_every_rcswitch_protocol(pid):
    code, bits = 0xA5C3F1, 24
    frame = encode_rcswitch(pid, code, bits)
    d = decode(frame)
    if pid == 9:
        # protocol 9 is protocol 8 with inverted levels; the same air signal is
        # decoded as protocol 8 (rc-switch tries 8 first too), so just require
        # a stable decode rather than a specific code
        assert d.protocol == "rcswitch-8" and d.bit_length == bits
        return
    assert d.protocol == f"rcswitch-{pid}"
    assert d.code == code and d.bit_length == bits


def test_decode_with_jitter_is_stable():
    frame = encode_rcswitch(1, 0x5A3C01, 24, 330)
    fps = {decode(_jitter(frame, 0.08)).fingerprint for _ in range(50)}
    assert fps == {"rcswitch-1:24:5913601"}


def test_unknown_protocol_falls_back_to_raw_and_is_stable():
    d = decode(RAW_PATTERN)
    assert d.protocol == "raw" and d.code is None
    assert d.fingerprint == raw_fingerprint(RAW_PATTERN)
    # the quantised signature must survive 5 % jitter
    assert all(decode(_jitter(RAW_PATTERN, 0.05)).fingerprint == d.fingerprint for _ in range(30))


def test_frames_match_tolerance():
    a = encode_rcswitch(1, 123456, 24)
    assert frames_match(a, _jitter(a, 0.1))
    assert not frames_match(a, encode_rcswitch(1, 123457, 24))
    assert not frames_match(a, a[:-2])


def test_parse_frame_line():
    assert parse_frame_line("F 1234 4 100 200 300 5000") == [100, 200, 300, 5000]
    assert parse_frame_line("F 1234 5 100 200 300 5000") is None  # count mismatch
    assert parse_frame_line("I rf_capture 1.0.0") is None
    assert parse_frame_line("garbage") is None


def test_engine_groups_repeats_and_drops_noise():
    events = []
    eng = CaptureEngine(on_event=events.append, min_repeats=2, settle_ms=10)
    frame = encode_rcswitch(1, 0xBEEF, 16, 350)
    noise = [random for random in range(100, 100 + 20 * 37, 37)] + [6000]
    eng._handle_line("F 1 %d %s" % (len(noise), " ".join(map(str, noise))))
    for _ in range(4):
        eng._handle_line("F 2 %d %s" % (len(frame), " ".join(map(str, _jitter(frame)))))
    # a different frame immediately after finishes the burst
    eng._handle_line("F 3 %d %s" % (len(noise), " ".join(map(str, noise))))
    caps = [e for e in events if e["type"] == "capture"]
    assert len(caps) == 1
    assert caps[0]["repeats"] == 4 and caps[0]["code"] == 0xBEEF and caps[0]["protocol"] == "rcswitch-1"
    assert eng.frames_dropped == 1


def test_database_roundtrip(tmp_path):
    db = Database(tmp_path / "t.sqlite")
    r = db.create_remote("Crompton fan", brand="Crompton")
    frame = encode_rcswitch(1, 0x5A3C01, 24, 330)
    d = decode(frame)
    b = db.create_button(r["id"], "Power", frame, d.fingerprint, d.protocol, d.code, d.bit_length, d.pulse_length, 5)
    assert b["remote_name"] == "Crompton fan" and b["timings"] == frame
    assert db.find_button_by_fingerprint(d.fingerprint)["name"] == "Power"
    assert db.list_remotes()[0]["button_count"] == 1
    cap = db.log_capture(frame, d.fingerprint, 3, d.protocol, d.code, d.bit_length, d.pulse_length, b["id"])
    assert db.recent_captures()[0]["button_name"] == "Power"
    exp = db.export()
    assert exp["remotes"][0]["buttons"][0]["code"] == 0x5A3C01
    assert db.delete_remote(r["id"]) and db.list_buttons() == []
    assert db.get_capture(cap["id"])["button_id"] is None  # ON DELETE SET NULL


def test_http_api(tmp_path, monkeypatch):
    monkeypatch.setenv("RC_DB", str(tmp_path / "api.sqlite"))
    for m in ("app",):
        sys.modules.pop(m, None)
    import app as appmod
    from fastapi.testclient import TestClient

    with TestClient(appmod.app) as c:
        assert c.get("/").status_code == 200
        r = c.post("/api/remotes", json={"name": "Test remote"}).json()
        frame = encode_rcswitch(1, 0x5A3C02, 24, 330)
        b = c.post("/api/buttons", json={"remote_id": r["id"], "name": "Speed +", "timings": frame})
        assert b.status_code == 201 and b.json()["code"] == 0x5A3C02
        dup = c.post("/api/buttons", json={"remote_id": r["id"], "name": "Again", "timings": _jitter(frame)})
        assert dup.status_code == 409
        assert c.post("/api/remotes", json={"name": "test REMOTE"}).status_code == 409
        exp = c.get("/api/export").json()
        assert exp["remotes"][0]["buttons"][0]["name"] == "Speed +"
        assert c.post("/api/command", json={"command": "?"}).status_code == 400  # not connected
        assert c.get("/api/ports").status_code == 200

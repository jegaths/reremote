import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from db import Database  # noqa: E402
from decoder import decode, encode_rcswitch  # noqa: E402
from matcher import button_masks, match_capture, stable_mask, widen_to_nibbles  # noqa: E402


def _btn(bid, name, codes, protocol="rcswitch-1", bits=32):
    return {"id": bid, "name": name, "remote_id": 1, "remote_name": "Fan", "protocol": protocol,
            "bit_length": bits, "fingerprint": f"{protocol}:{bits}:{codes[0]}",
            "samples": [{"code": c, "fingerprint": f"{protocol}:{bits}:{c}"} for c in codes],
            "sample_count": len(codes)}


def _cap(code, bits=32, protocol="rcswitch-1"):
    return {"protocol": protocol, "code": code, "bit_length": bits, "fingerprint": f"{protocol}:{bits}:{code}"}


def test_stable_mask_and_nibble_widening():
    raw = stable_mask([0x11C0E0F, 0x11C0E1E, 0x11C0E2D], 32)
    assert raw == 0xFFFFFFCC                      # bits 2,3,6,7 agree by chance
    assert widen_to_nibbles(raw, 32) == 0xFFFFFF00  # whole low byte treated as varying
    assert stable_mask([0xFF], 8) == 0xFF


def test_exact_match_wins_even_with_one_sample():
    on = _btn(1, "On", [0x11C0E69])
    m = match_capture(_cap(0x11C0E69), [on])
    assert m and m["method"] == "exact" and m["button_name"] == "On"


def test_single_sample_cannot_pattern_match():
    on = _btn(1, "On", [0x11C0E69])
    assert match_capture(_cap(0x11C0E1E), [on]) is None


def test_pattern_match_after_learning_crompton_counter():
    on = _btn(1, "On", [0x11C0E69, 0x11C0E1E])     # low byte = 15 * counter
    off = _btn(2, "Off", [0x11C0678])              # single sample: borrows On's varying field
    assert button_masks([on, off]) == {1: 0xFFFFFF00, 2: 0xFFFFFF00}
    m = match_capture(_cap(0x11C0E2D), [on, off])
    assert m and m["method"] == "pattern" and m["button_name"] == "On" and m["mask_hex"] == "0xFFFFFF00"
    m = match_capture(_cap(0x11C06B4), [on, off])
    assert m and m["button_name"] == "Off" and m["method"] == "pattern"
    # a press whose counter byte has its top bit set (never seen before) still matches
    assert match_capture(_cap(0x11C0E87), [on, off])["button_name"] == "On"
    # a different remote address never matches
    assert match_capture(_cap(0x22C0E2D), [on, off]) is None
    # different protocol / length never matches
    assert match_capture(_cap(0x11C0E2D, bits=24), [on, off]) is None


def test_unstable_button_is_not_trusted():
    noisy = _btn(1, "X", [0x00000000, 0xFFFF0000, 0x0000FFFF])  # only 0 bits stable
    assert match_capture(_cap(0x12345678), [noisy]) is None


def test_samples_persist_and_export(tmp_path):
    db = Database(tmp_path / "s.sqlite")
    r = db.create_remote("Fan")
    f1 = encode_rcswitch(1, 0x11C0E69, 32, 266)
    d1 = decode(f1)
    b = db.create_button(r["id"], "On", f1, d1.fingerprint, d1.protocol, d1.code, d1.bit_length, d1.pulse_length)
    assert b["sample_count"] == 1
    f2 = encode_rcswitch(1, 0x11C0E1E, 32, 266)
    d2 = decode(f2)
    b = db.add_sample(b["id"], f2, d2.fingerprint, d2.code)
    assert b["sample_count"] == 2
    assert db.find_button_by_fingerprint(d2.fingerprint)["name"] == "On"
    assert len(db.export()["remotes"][0]["buttons"][0]["samples"]) == 2
    bw = db.buttons_with_samples()
    assert match_capture(_cap(0x11C0E2D), bw)["button_name"] == "On"

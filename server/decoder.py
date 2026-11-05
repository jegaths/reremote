"""Protocol decoding and fingerprinting for raw OOK pulse trains.

A frame, as produced by the firmware, is a list of microsecond durations:
    [H, L, H, L, ..., H, GAP]
starting with the first HIGH pulse after the inter-frame silence and ending
with that silence (the long LOW "gap").

The rc-switch protocol table is reproduced here so the common fixed-code chips
(PT2262, EV1527, HS1527, SC5262, ...) are decoded into protocol/code/bits, the
same numbers the rc-switch Arduino library (and therefore Tasmota, ESPHome,
OpenMQTTGateway, Home Assistant integrations) would report. Anything else is
kept as 'raw' with a timing fingerprint, which is still replayable.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, asdict
from typing import Optional

# (id, pulse_us, sync(high,low), zero(high,low), one(high,low), inverted)
RCSWITCH_PROTOCOLS = [
    (1, 350, (1, 31), (1, 3), (3, 1), False),
    (2, 650, (1, 10), (1, 2), (2, 1), False),
    (3, 100, (30, 71), (4, 11), (9, 6), False),
    (4, 380, (1, 6), (1, 3), (3, 1), False),
    (5, 500, (6, 14), (1, 2), (2, 1), False),
    (6, 450, (23, 1), (1, 2), (2, 1), True),
    (7, 150, (2, 62), (1, 6), (6, 1), False),
    (8, 200, (3, 130), (7, 16), (3, 16), False),
    (9, 200, (130, 7), (16, 7), (16, 3), True),
    (10, 365, (18, 1), (3, 1), (1, 3), True),
    (11, 270, (36, 1), (1, 2), (2, 1), True),
    (12, 320, (36, 1), (1, 2), (2, 1), True),
]

RECEIVE_TOLERANCE = 0.60  # same as rc-switch's nReceiveTolerance (60 %)


@dataclass
class Decoded:
    protocol: str            # 'rcswitch-<n>' or 'raw'
    code: Optional[int]      # decoded integer when protocol != raw
    bit_length: Optional[int]
    pulse_length: Optional[int]
    fingerprint: str
    error: float             # mean relative timing error, lower is better

    def to_dict(self) -> dict:
        d = asdict(self)
        d["code_hex"] = f"0x{self.code:X}" if self.code is not None else None
        d["code_bin"] = (format(self.code, f"0{self.bit_length}b")
                         if self.code is not None and self.bit_length else None)
        return d


def _try_protocol(frame: list[int], proto) -> Optional[tuple[int, int, int, float]]:
    """Return (code, bits, pulse_us, error) or None if the frame does not fit."""
    pid, _nominal, sync, zero, one, inverted = proto
    if len(frame) < 2 * 8 + 1:
        return None
    # Rotate so the long gap is first, like rc-switch's timings[0]
    t = [frame[-1]] + frame[:-1]
    sync_len = max(sync)
    delay = t[0] / sync_len
    if delay < 50 or delay > 5000:
        return None
    tol = delay * RECEIVE_TOLERANCE
    first = 2 if inverted else 1
    # the trailing sync pulse (1 element for non-inverted, none to spare for
    # inverted since index 1 is the sync's short half) is excluded from data
    data = t[first:len(t) - 1] if not inverted else t[first:]
    if len(data) < 2:
        return None
    if len(data) % 2:
        data = data[:-1]
    code = 0
    bits = 0
    err_sum = 0.0
    for i in range(0, len(data), 2):
        h, l = data[i], data[i + 1]
        z_ok = abs(h - delay * zero[0]) < tol and abs(l - delay * zero[1]) < tol
        o_ok = abs(h - delay * one[0]) < tol and abs(l - delay * one[1]) < tol
        if z_ok and not o_ok:
            code <<= 1
            err_sum += abs(h - delay * zero[0]) / (delay * zero[0]) + abs(l - delay * zero[1]) / (delay * zero[1])
        elif o_ok and not z_ok:
            code = (code << 1) | 1
            err_sum += abs(h - delay * one[0]) / (delay * one[0]) + abs(l - delay * one[1]) / (delay * one[1])
        else:
            return None
        bits += 1
    if bits < 8:
        return None
    return code, bits, int(round(delay)), err_sum / (2 * bits)


def pulse_classes(frame: list[int], rel_tol: float = 0.35) -> tuple[list[int], list[int]]:
    """Group the pulse widths of a frame into classes (short/medium/long...).

    Returns (class_index_per_pulse, class_mean_per_class). Widths within
    `rel_tol` of a class mean join that class. The trailing gap is excluded
    and reported separately by callers."""
    body = frame[:-1] if len(frame) > 1 else list(frame)
    order = sorted(set(body))
    centres: list[float] = []
    members: list[list[int]] = []
    for d in order:
        if centres and d <= centres[-1] * (1 + rel_tol) + 40:
            members[-1].append(d)
            centres[-1] = sum(members[-1]) / len(members[-1])
        else:
            centres.append(float(d))
            members.append([d])
    lookup = {d: i for i, m in enumerate(members) for d in m}
    return [lookup[d] for d in body], [int(round(c)) for c in centres]


def raw_fingerprint(frame: list[int]) -> str:
    """Identifier that is stable across captures of the same button: the
    sequence of pulse-width classes (a=shortest, b=next, ...). The inter-frame
    gap is deliberately left out: it never distinguishes buttons and its
    rounding would flip under jitter."""
    classes, centres = pulse_classes(frame)
    sig = "".join(chr(97 + min(c, 25)) for c in classes) + f"|{len(centres)}"
    return "raw-" + hashlib.sha1(sig.encode()).hexdigest()[:12]


def decode(frame: list[int]) -> Decoded:
    best = None
    best_key = None
    for proto in RCSWITCH_PROTOCOLS:
        r = _try_protocol(frame, proto)
        if r is None:
            continue
        code, bits, pulse, err = r
        # lowest timing error wins; protocols that differ only in nominal
        # pulse width (11 vs 12) tie, so prefer the closer nominal width
        key = (round(err, 3), abs(pulse - proto[1]))
        if best is None or key < best_key:
            best, best_key = (proto[0], code, bits, pulse, err), key
    if best:
        pid, code, bits, pulse, err = best
        return Decoded(
            protocol=f"rcswitch-{pid}", code=code, bit_length=bits,
            pulse_length=pulse, fingerprint=f"rcswitch-{pid}:{bits}:{code}", error=err,
        )
    return Decoded(protocol="raw", code=None, bit_length=None, pulse_length=None,
                   fingerprint=raw_fingerprint(frame), error=0.0)


def frames_match(a: list[int], b: list[int], rel_tol: float = 0.30, abs_tol: int = 120) -> bool:
    """True when two frames are the same button press within jitter."""
    if len(a) != len(b):
        return False
    for x, y in zip(a, b):
        if abs(x - y) > max(abs_tol, rel_tol * max(x, y)):
            return False
    return True


def average_frames(frames: list[list[int]]) -> list[int]:
    n = len(frames)
    return [int(round(sum(f[i] for f in frames) / n)) for i in range(len(frames[0]))]


def encode_rcswitch(protocol_id: int, code: int, bits: int, pulse: Optional[int] = None) -> list[int]:
    """Build a frame (firmware layout) for a given rc-switch code. Used by the
    simulator and tests, and handy later for replaying with a transmitter."""
    proto = next(p for p in RCSWITCH_PROTOCOLS if p[0] == protocol_id)
    _, nominal, sync, zero, one, inverted = proto
    d = pulse or nominal
    out: list[int] = []
    for i in range(bits - 1, -1, -1):
        h, l = one if (code >> i) & 1 else zero
        out += [h * d, l * d]
    if inverted:
        # frame layout wants the long element last; for inverted protocols the
        # separator is the HIGH half of the sync, the LOW half (short) precedes data
        return [sync[1] * d] + out + [sync[0] * d]
    return out + [sync[0] * d, sync[1] * d]

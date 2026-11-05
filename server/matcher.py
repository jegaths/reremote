"""Recognise a capture as a saved button even when the remote changes some
bits on every press (a rolling counter or checksum in part of the code).

1. Exact match: the fingerprint equals a button's or one of its samples'.
2. Pattern match: for each button with two or more samples we compute the bits
   that stayed constant across its samples. The varying bits are widened to
   whole nibbles, because counters and checksums live in fields, not in
   scattered bits, and a handful of samples can leave a bit of a field looking
   stable by chance. The union of varying nibbles over a remote's buttons is
   the remote's "varying field", and it is applied to buttons of that remote
   that have only one sample so far. A capture matches a button when it agrees
   on every stable bit, at least half the bits are stable, and exactly one
   button of that protocol/bit length qualifies.
"""
from __future__ import annotations

from typing import Any, Optional

MIN_STABLE_FRACTION = 0.5


def stable_mask(codes: list[int], bits: int) -> int:
    """Bits identical across all codes."""
    full = (1 << bits) - 1
    mask = full
    for c in codes[1:]:
        mask &= ~(codes[0] ^ c) & full
    return mask


def widen_to_nibbles(mask: int, bits: int) -> int:
    """Clear every nibble that has at least one cleared bit."""
    full = (1 << bits) - 1
    out = mask & full
    for shift in range(0, bits, 4):
        nib = 0xF << shift
        if (mask & nib) != (nib & full):
            out &= ~nib
    return out & full


def button_masks(buttons: list[dict[str, Any]]) -> dict[int, int]:
    """button id -> stable mask, for buttons that can be pattern matched."""
    learned: dict[int, int] = {}
    remote_varying: dict[tuple[int, str, int], int] = {}
    for b in buttons:
        bits = b.get("bit_length")
        codes = [s["code"] for s in b.get("samples", []) if s.get("code") is not None]
        if not bits or len(codes) < 2:
            continue
        m = widen_to_nibbles(stable_mask(codes, bits), bits)
        learned[b["id"]] = m
        key = (b["remote_id"], b["protocol"], bits)
        remote_varying[key] = remote_varying.get(key, 0) | (~m & ((1 << bits) - 1))
    for b in buttons:
        bits = b.get("bit_length")
        if not bits or b["id"] in learned:
            continue
        key = (b["remote_id"], b["protocol"], bits)
        if key in remote_varying and remote_varying[key]:
            learned[b["id"]] = ((1 << bits) - 1) & ~remote_varying[key]
    return learned


def match_capture(decoded: dict[str, Any], buttons: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    fp = decoded["fingerprint"]
    for b in buttons:
        if b["fingerprint"] == fp or any(s["fingerprint"] == fp for s in b.get("samples", [])):
            return _result(b, "exact", None)

    code, bits = decoded.get("code"), decoded.get("bit_length")
    if code is None or not bits:
        return None
    masks = button_masks(buttons)
    candidates = []
    for b in buttons:
        if b["protocol"] != decoded["protocol"] or b["bit_length"] != bits or b["id"] not in masks:
            continue
        mask = masks[b["id"]]
        if bin(mask).count("1") < bits * MIN_STABLE_FRACTION:
            continue
        ref = next((s["code"] for s in b.get("samples", []) if s.get("code") is not None), b.get("code"))
        if ref is not None and (code & mask) == (ref & mask):
            candidates.append((bin(mask).count("1"), b, mask))
    if not candidates:
        return None
    candidates.sort(key=lambda x: -x[0])
    best_bits, best, mask = candidates[0]
    if len(candidates) > 1 and candidates[1][0] == best_bits:
        return None  # ambiguous between two buttons
    return _result(best, "pattern", mask)


def _result(b: dict[str, Any], method: str, mask: Optional[int]) -> dict[str, Any]:
    return {"button_id": b["id"], "button_name": b["name"], "remote_id": b["remote_id"],
            "remote_name": b["remote_name"], "method": method,
            "mask_hex": f"0x{mask:X}" if mask is not None else None,
            "sample_count": b.get("sample_count", len(b.get("samples", [])))}

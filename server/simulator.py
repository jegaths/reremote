"""Fake Arduino: yields firmware-style serial lines so the whole pipeline can be
exercised without hardware. Emits noise frames (dropped because they never
repeat), EV1527-style 24-bit presses, and an unknown 'raw' protocol press so the
UI shows both decode paths."""
from __future__ import annotations

import random
import threading
import time
from typing import Iterator, Optional

from decoder import encode_rcswitch

SIM_BUTTONS = [
    ("rcswitch", 1, 0x5A3C01, 24, 330),   # imaginary fan remote: power
    ("rcswitch", 1, 0x5A3C02, 24, 330),   # speed +
    ("rcswitch", 1, 0x5A3C04, 24, 330),   # speed -
    ("rcswitch", 1, 0x5A3C08, 24, 330),   # light
    ("raw", None, None, None, None),      # something rc-switch cannot decode
]

RAW_PATTERN = [250, 750, 750, 250, 250, 250, 1250, 750, 250, 1250, 750, 750,
               250, 750, 750, 250, 250, 250, 1250, 750, 250, 1250, 750, 750,
               250, 750, 750, 250, 250, 250, 1250, 750, 250, 1250, 750, 750,
               250, 750, 750, 250, 250, 8200]  # three pulse widths: no 2-symbol protocol fits


def _jitter(frame: list[int], pct: float = 0.06) -> list[int]:
    return [max(50, int(round(d * random.uniform(1 - pct, 1 + pct)))) for d in frame]


def _line(frame: list[int], ms: int) -> str:
    return f"F {ms} {len(frame)} " + " ".join(map(str, frame))


def simulated_lines(stop: threading.Event) -> Iterator[Optional[str]]:
    t0 = time.time()
    yield "I rf_capture 1.1.0-sim board=simulator pin=0 gap=4000 min_pulse=80 min_edges=16"
    while not stop.is_set():
        # idle noise: a few random single frames that never repeat
        for _ in range(random.randint(0, 2)):
            noise = [random.randint(90, 900) for _ in range(random.randint(16, 40))] + [random.randint(4000, 9000)]
            yield _line(noise, int((time.time() - t0) * 1000))
            time.sleep(random.uniform(0.2, 0.6))
            yield None
        # a button press: 4-8 repeats of the same frame
        kind, proto, code, bits, pulse = random.choice(SIM_BUTTONS)
        frame = encode_rcswitch(proto, code, bits, pulse) if kind == "rcswitch" else RAW_PATTERN
        for _ in range(random.randint(4, 8)):
            if stop.is_set():
                return
            yield _line(_jitter(frame), int((time.time() - t0) * 1000))
            time.sleep(sum(frame) / 1_000_000)
        # quiet period lets the burst settle
        for _ in range(int(random.uniform(2.5, 5.0) / 0.1)):
            if stop.is_set():
                return
            time.sleep(0.1)
            yield None

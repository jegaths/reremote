"""Print the serial port of the attached board and a guess of which board it is.

Usage: findport.py [port|board|both|list]   (default: both, as "PORT BOARD")
BOARD is one of: esp32s3, nano, nano-old, unknown. Exit 1 when nothing found.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
from capture import list_serial_ports  # noqa: E402

GUESS = [
    ("ESP32-S3 native", "esp32s3"),
    ("ESP32 dev board", "esp32s3"),
    ("CH340", "nano-old"),      # CH340 clones nearly always ship the old bootloader
    ("FTDI", "nano"),
    ("Arduino", "nano"),
]


def main() -> int:
    what = sys.argv[1] if len(sys.argv) > 1 else "both"
    ports = list_serial_ports()
    if what == "list":
        for x in ports:
            print(f"{x['device']:36} {x['description']}")
        if not ports:
            print("no serial ports found")
        return 0
    if not ports:
        print("no board found: plug it in via USB and try again", file=sys.stderr)
        return 1
    p = ports[0]
    board = next((b for key, b in GUESS if key.lower() in p["description"].lower()), "unknown")
    if len(ports) > 1:
        print("several ports found, using the first; override with PORT=...: "
              + ", ".join(x["device"] for x in ports), file=sys.stderr)
    out = {"port": p["device"], "board": board, "both": f"{p['device']} {board}"}[what]
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())

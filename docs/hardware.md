# Hardware

Either board works for capturing. The server doesn't care which one is
plugged in. The firmware announces itself (`board=nano` or `board=esp32s3`)
and the UI shows it next to the port name. Only an ESP32 can run the Home
Assistant bridge.

Supported ESP32 chips: **ESP32 (classic), ESP32-S2, ESP32-S3, ESP32-C3,
ESP32-C6**. All of them have WiFi, use 3.3 V logic and default to **GPIO 4**
for the receiver. Only the ESP32-S3 has been tested on real hardware so far.
The others compile from the same sketches.

Use a breadboard and jumper wires, or solder the three wires directly.

**Unplug USB before changing any wiring.**

## Option A: Arduino Nano

| Receiver pin | Nano pin |
|--------------|----------|
| VCC          | 5V       |
| GND          | GND      |
| DAT          | D2       |
| ANT          | optional: 17.3 cm straight wire |

## Option B: any ESP32

| Receiver pin | ESP32 pin |
|--------------|--------------|
| VCC          | **3V3** (not 5V / VBUS, see below) |
| GND          | GND          |
| DAT          | GPIO 4       |
| ANT          | optional: 17.3 cm straight wire |

ESP32 chips use 3.3 V logic, and their GPIOs are not 5 V tolerant. Powering
the receiver from 3V3 keeps the DAT line at 3.3 V. If range is poor at 3.3 V,
power the receiver from 5V and put a voltage divider on DAT (2.2 kΩ from DAT to
GPIO 4, 3.3 kΩ from GPIO 4 to GND). GPIO 4 is safe on every supported chip.
To use another pin, change `RX_PIN` in the sketch (or the DAT pin field on the
Home Assistant tab). Avoid the pins that affect booting, USB or flash:

| Chip | Avoid |
|------|-------|
| ESP32 (classic) | 0, 2, 5, 12, 15 (strapping), 6–11 (flash) |
| ESP32-S2 | 0, 45, 46 (strapping), 19, 20 (USB) |
| ESP32-S3 | 0, 3, 45, 46 (strapping), 19, 20 (USB) |
| ESP32-C3 | 2, 8, 9 (strapping), 18, 19 (USB) |
| ESP32-C6 | 8, 9, 15 (strapping), 12, 13 (USB) |

Eight-pin receivers (RXB6 / RXB12 layout: `GND DAT NC VCC` on one side,
`VCC GND GND ANT` on the other) have duplicated supply pins. Connect one VCC,
one GND and DAT (plus ANT if you add an antenna), and leave the rest empty.

**The antenna is optional.** Most receiver modules pick up a remote in the
same room without one. If captures only work up close, or you see mostly
noise, solder or push a straight 17.3 cm wire (a quarter wavelength at
433.92 MHz) into the ANT pin; it greatly improves range.

## First run, step by step (ESP32)

Flashing needs only the USB cable, so do that first and wire the receiver
afterwards.

1. Plug the bare board into the computer with a USB **data** cable. Some
   cables are charge-only and won't show a port.
2. `make setup` (once).
3. `make ports` should list a port (`/dev/cu.usbmodem…` or
   `/dev/cu.usbserial…` on macOS, `/dev/ttyACM0` or `/dev/ttyUSB0` on Linux)
   and a guessed board type.
4. `make flash`, or `make flash BOARD=esp32c3` (etc.) if the guess isn't your
   chip. If it ends with "Failed to connect", hold **BOOT**, tap
   **RESET**, release BOOT, and run `make flash` again. If the LED keeps
   cycling colours after flashing, or the log says `waiting for download`,
   unplug and replug the cable.
5. Optional check: `make run`, pick the port, and press Connect. The status
   bar should show the board and a firmware line. Press Ctrl-C to stop.
6. **Unplug USB, then wire the receiver** as in the table above. Double-check
   that nothing connects 3V3 directly to GND.
7. Plug USB back in, run `make run`, and start capturing.

The frame counter in the status bar ticking now and then from receiver noise
is normal.

## Flashing by hand

`make flash` does all of this for you. These are the equivalent commands if
you prefer the Arduino IDE or arduino-cli directly.

### Arduino Nano

Arduino IDE: choose board "Arduino Nano" and processor "ATmega328P", or
"ATmega328P (Old Bootloader)" for most clones. Or:

```sh
arduino-cli compile --fqbn arduino:avr:nano firmware/rf_capture
arduino-cli upload  --fqbn arduino:avr:nano -p <PORT> firmware/rf_capture
# clones with the old bootloader:
arduino-cli upload  --fqbn arduino:avr:nano:cpu=atmega328old -p <PORT> firmware/rf_capture
```

### ESP32 boards

| Chip | Arduino IDE board | arduino-cli FQBN |
|------|-------------------|------------------|
| ESP32 (classic) | ESP32 Dev Module | `esp32:esp32:esp32` |
| ESP32-S2 | ESP32S2 Dev Module, USB CDC On Boot: Enabled | `esp32:esp32:esp32s2:CDCOnBoot=cdc` |
| ESP32-S3 | ESP32S3 Dev Module, USB CDC On Boot: Enabled | `esp32:esp32:esp32s3:CDCOnBoot=cdc` |
| ESP32-C3 | ESP32C3 Dev Module, USB CDC On Boot: Enabled | `esp32:esp32:esp32c3:CDCOnBoot=cdc` |
| ESP32-C6 | ESP32C6 Dev Module, USB CDC On Boot: Enabled | `esp32:esp32:esp32c6:CDCOnBoot=cdc` |

In the Arduino IDE, install the "esp32 by Espressif" board package first. With
arduino-cli:

```sh
arduino-cli core install esp32:esp32   # once, after adding the Espressif board URL
arduino-cli compile --fqbn <FQBN> firmware/rf_capture
arduino-cli upload  --fqbn <FQBN> -p <PORT> firmware/rf_capture
```

Many S2, S3, C3 and C6 boards have two USB-C sockets: one on the chip's native
USB (often labelled "USB") and one through a UART bridge chip ("COM" or
"UART"). The capture sketch sends its output to both, so either socket works
for capturing. A classic ESP32 has only the UART bridge.

**Prefer the native USB socket** for flashing. It appears as "ESP32 native
USB" in the UI. On at least one tested board, the UART-bridge socket (CP2102)
never answered esptool, even in manual download mode.

If the serial log after an upload shows `boot:0x0 (DOWNLOAD...)` and
`waiting for download`, the chip is stuck in download mode. esptool's reset
over native USB doesn't always get it out. Press RESET once, or unplug and
replug the USB cable. The colour-cycling LED on a fresh board is the factory
demo; it stops once this firmware is running.

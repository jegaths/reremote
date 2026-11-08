# Hardware

Either board works for capturing. The server doesn't care which one is
plugged in. The firmware announces itself (`board=nano` or `board=esp32s3`)
and the UI shows it next to the port name. Only the ESP32-S3 can run the Home
Assistant bridge.

**Unplug USB before touching any jumper.**

## Option A: Arduino Nano

| Receiver pin | Nano pin |
|--------------|----------|
| VCC          | 5V       |
| GND          | GND      |
| DAT          | D2       |
| ANT          | 17.3 cm straight wire |

## Option B: ESP32-S3

| Receiver pin | ESP32-S3 pin |
|--------------|--------------|
| VCC          | **3V3** (not 5V / VBUS, see below) |
| GND          | GND          |
| DAT          | GPIO 4       |
| ANT          | 17.3 cm straight wire |

The ESP32-S3 uses 3.3 V logic, and its GPIOs are not 5 V tolerant. Powering
the receiver from 3V3 keeps the DAT line at 3.3 V. If range is poor at 3.3 V,
power the receiver from 5V and put a voltage divider on DAT (2.2 kΩ from DAT to
GPIO 4, 3.3 kΩ from GPIO 4 to GND). To use another GPIO, change `RX_PIN` in
the sketch. Avoid the strapping pins 0, 3, 45 and 46 and the USB pins 19
and 20.

Eight-pin receivers (RXB6 / RXB12 layout: `GND DAT NC VCC` on one side,
`VCC GND GND ANT` on the other) have duplicated supply pins. Connect one VCC,
one GND, DAT and ANT, and leave the rest empty.

**The antenna matters more than anything else.** Without one, the receiver
only picks up the remote within a few centimetres and reports mostly noise.
A straight 17.3 cm wire (a quarter wavelength at 433.92 MHz) is enough.

## First run, step by step (ESP32-S3)

Flashing needs only the USB cable, so do that first and wire the receiver
afterwards.

1. Plug the bare board into the computer with a USB **data** cable. Some
   cables are charge-only and won't show a port.
2. `make setup` (once).
3. `make ports` should list a port (`/dev/cu.usbmodem…` on macOS,
   `/dev/ttyACM0` or `/dev/ttyUSB0` on Linux) and `detected: … esp32s3`.
4. `make flash`. If it ends with "Failed to connect", hold **BOOT**, tap
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

### ESP32-S3

Most S3 dev boards have two USB-C sockets: one on the native USB peripheral
(often labelled "USB") and one through a UART bridge chip ("COM" or "UART").
The sketch sends its output to both, so either socket works for capturing.

Arduino IDE: install the "esp32 by Espressif" board package, choose "ESP32S3
Dev Module", set **USB CDC On Boot: Enabled**, pick the port, and click
Upload. Or:

```sh
arduino-cli core install esp32:esp32   # once, after adding the Espressif board URL
arduino-cli compile --fqbn esp32:esp32:esp32s3:CDCOnBoot=cdc firmware/rf_capture
arduino-cli upload  --fqbn esp32:esp32:esp32s3:CDCOnBoot=cdc -p <PORT> firmware/rf_capture
```

**Prefer the native USB socket** for flashing. It appears as "ESP32-S3 native
USB" in the UI. On at least one tested board, the UART-bridge socket (CP2102)
never answered esptool, even in manual download mode.

If the serial log after an upload shows `boot:0x0 (DOWNLOAD...)` and
`waiting for download`, the chip is stuck in download mode. esptool's reset
over native USB doesn't always get it out. Press RESET once, or unplug and
replug the USB cable. The colour-cycling LED on a fresh board is the factory
demo; it stops once this firmware is running.

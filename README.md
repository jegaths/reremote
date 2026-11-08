# ReRemote

**Turn any old 433 MHz remote into a Home Assistant controller. No YAML, no MQTT broker.**

Plug a cheap ESP32 or Arduino Nano with a 433 MHz receiver into your
computer, press the buttons on your fan, light or plug-socket remote, and give
each one a name in the browser. Then map the buttons to Home Assistant
entities and flash the board as a standalone WiFi bridge, from the same page.

```
remote ──433 MHz──> receiver ──> ESP32 / Nano ──USB──> ReRemote (browser UI)
                                                               │
                                     map buttons to HA entities, flash over USB/WiFi
                                                               ▼
remote ──433 MHz──> receiver ──> ESP32 bridge ──WiFi──> Home Assistant
```

<!-- TODO: add a screenshot or GIF of a capture card and the Home Assistant tab here -->

## Features

- **Capture in the browser.** Press a button and you get a card with the
  decoded code. Name it and save it. Press it again to confirm it is recognised.
- **Decodes common chips.** It uses the rc-switch protocol table (PT2262,
  EV1527, HS1527, SC5262 and others), so codes match what Tasmota, ESPHome and
  OpenMQTTGateway report. Anything unknown is kept as raw timings.
- **Learns remotes with counters.** Some remotes change part of the code on
  every press. From two presses the matcher works out which bits stay the same
  and ignores the rest.
- **Home Assistant mapping.** Pick an entity and an action for each button
  (toggle, on/off, fan speed, brightness, scenes, scripts) and test it
  instantly from the page.
- **One-click bridge firmware.** The tool generates the config, compiles and
  flashes over USB. After that, updates go **over WiFi**.
- **Built-in firmware editor.** Edit, compile and flash any sketch in
  `firmware/` from the Firmware tab.
- **Simulator.** `make sim` runs the whole UI without any hardware.
- Everything stays local: SQLite on your machine, nothing goes to the cloud.

## What you need

| Part | Notes | Approx. cost |
|------|-------|--------------|
| Any WiFi ESP32 dev board (ESP32, S2, S3, C3, C6) **or** an Arduino Nano | An ESP32 is needed for the WiFi Home Assistant bridge; a Nano can only capture | $3–8 |
| 433 MHz receiver (RXB6, RW-54, SRX882 or similar superheterodyne module) | Avoid the cheap green "XY-MK-5V" regenerative boards if you can; they are very noisy | $1–3 |
| 17.3 cm piece of straight wire (optional) | Antenna. Not needed with the remote in the same room; add it if range is poor | — |
| USB **data** cable | Needed to flash the board and to capture. Charge-only cables don't work. After the bridge is installed, updates go over WiFi | — |
| Breadboard + 3 jumper wires (optional) | Or solder the receiver straight to the board | — |

**For the Home Assistant part** you also need a running
[Home Assistant](https://www.home-assistant.io/) instance on the same network,
and the thing you want the remote to control (a smart switch, smart plug,
light, fan, scene or script) must **already be set up in Home Assistant** as
an entity. ReRemote doesn't add devices to Home Assistant. It makes your old
remote's buttons call actions on entities you already have. Capturing and
building the button library work without Home Assistant.

Wiring (full details and pin notes in [docs/hardware.md](docs/hardware.md)):

| Receiver | Any ESP32 | Arduino Nano |
|----------|----------|--------------|
| VCC | 3V3 | 5V |
| GND | GND | GND |
| DAT | GPIO 4 | D2 |
| ANT (optional) | 17.3 cm wire | 17.3 cm wire |

## Quick start

Works on **macOS and Linux**. Needs Python 3.10+, `make`, `curl` and `git`.
`make setup` installs everything else (Python packages into a local `.venv`,
and arduino-cli with the board cores if you don't have it).

```sh
git clone https://github.com/jegaths/reremote.git
cd reremote
make setup     # once
make flash     # plug the board in first; detects the port and board type
make run       # opens http://127.0.0.1:8000
```

No hardware yet? `make sim` starts the UI with a fake board.

Then in the browser:

1. Pick the port and press **Connect**.
2. Hold the remote about a metre away and press a button for half a second.
   A card appears under *Live captures*.
3. Choose **+ New remote…**, name it (e.g. "Bedroom fan"), name the button
   (e.g. "Power"), and press **Save**.
4. Press the same button again. The card should now show a green label with
   its name. That confirms the code is good.
5. Repeat for every button, then go to the **Home Assistant** tab
   ([guide](docs/home-assistant.md)) to map each button to an entity that
   already exists in your Home Assistant.

`make help` lists every command. The board type is guessed from USB. For an
ESP32-S2, C3 or C6, or if the guess is wrong, name it:
`make flash BOARD=esp32|esp32s2|esp32s3|esp32c3|esp32c6|nano|nano-old`
(add `PORT=/dev/ttyACM0` to pick the port). In the browser, the Firmware tab
has the same board picker.

### Platform notes

- **Linux:** to access serial ports, add yourself to the `dialout` group
  (`sudo usermod -aG dialout $USER`), then log out and back in.
  `make ports` reminds you if you aren't in the group.
- **macOS:** the first time you use the Home Assistant tab, macOS asks whether
  Python may find devices on your local network. Click **Allow**.
- **Windows:** not tested yet. The server is plain Python and should run:
  `python -m venv .venv`, `.venv\Scripts\pip install -r server/requirements.txt`,
  `.venv\Scripts\python server/app.py`. Install
  [arduino-cli](https://arduino.github.io/arduino-cli/latest/installation/)
  yourself for flashing. Reports and PRs are welcome.

## Troubleshooting

| Symptom | Likely cause |
|---------|--------------|
| Frame counter never moves, even with the remote right next to the receiver | The remote is not on 433 MHz (many newer fan remotes use 2.4 GHz). See [Limitations](#limitations). |
| Counter ticks but no cards appear, or only at short range | Move the receiver away from the laptop and USB cable, add a 17.3 cm wire antenna, and set `min repeats` to 1 to see raw frames. |
| Every press of the same button shows a different code | The remote uses a counter or a rolling code. Use **same as: <button>** to teach a second press ([details](docs/how-it-works.md#remotes-that-change-the-code-on-every-press)). |
| ESP32 flash ends with "Failed to connect" | Hold **BOOT**, tap **RESET**, release BOOT, then run `make flash` again. On boards with two USB sockets, use the native USB one. |
| Flash says the chip is a different ESP32 type | The board was guessed wrong. Pass `BOARD=` (e.g. `BOARD=esp32c3`) or pick the board in the Firmware tab. |
| No serial port is listed | Try another cable (many are charge-only) or the other USB socket. On Linux, check the `dialout` group. |

More detail is in [docs/hardware.md](docs/hardware.md).

## Limitations

Please read these before buying parts:

- **433.92 MHz ASK/OOK only.** Remotes on 315 MHz, 868 MHz, 2.4 GHz,
  Bluetooth or infrared are not supported yet. Check the label in the battery
  compartment or the chip on the PCB: SYN115, EV1527 and HS1527 mean 433 MHz;
  nRF24, A7105 and XN297 mean 2.4 GHz.
- **Fixed codes and simple counters only.** Real rolling-code (KeeLoq-style)
  remotes, such as most garage doors and car keys, cannot be matched. That is
  intentional.
- **Receive only.** The bridge listens to your remote and calls Home
  Assistant. It does not transmit yet, so Home Assistant cannot drive the
  original device's own receiver.
- **The Home Assistant bridge needs an ESP32 with WiFi.** It builds for the
  ESP32, S2, S3, C3 and C6. It has been tested on real hardware only on the
  **ESP32-S3** so far; reports for the other chips are welcome. ESP8266 and
  the ESP32-H2 (no WiFi) are not supported. The Nano works for capturing.
- **Raw-timing buttons** (codes the rc-switch table can't decode) are stored in
  the library but are left out of the bridge firmware.
- Only use it with remotes and devices you own.

## Roadmap

The capture → name → map to Home Assistant workflow is not tied to 433 MHz.
Ideas, roughly in order:

- [ ] **Infrared remotes** (TV, AC, LED strips) with a $1 IR receiver, in the
      same UI
- [ ] **Transmit / replay** with a 433 MHz transmitter, so Home Assistant can
      control the original device too
- [ ] **Other bands:** 315 MHz and 868 MHz receivers
- [ ] **Export to ESPHome YAML** for people who already run ESPHome
- [ ] **Several bridges** around the house, managed from one page
- [ ] Confirmed hardware tests on ESP32, S2, C3 and C6; ESP8266 support; a
      pre-built Windows setup
- [ ] Home Assistant add-on packaging

Have a remote that doesn't work, or an idea? Please
[open an issue](https://github.com/jegaths/reremote/issues).

## Documentation

- [docs/hardware.md](docs/hardware.md): wiring, receivers, antenna, and
  flashing each board by hand
- [docs/home-assistant.md](docs/home-assistant.md): mapping buttons, the
  bridge, and over-the-air updates
- [docs/how-it-works.md](docs/how-it-works.md): the firmware protocol,
  decoding, counter learning, HTTP API, and code layout

## Contributing

Bug reports, remote compatibility reports ("my X remote works / doesn't
work") and pull requests are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).
Pull requests need agreement to the short contributor licence terms there.

## License

Copyright © 2026 Jegath S.

ReRemote is free software, licensed under the
**GNU Affero General Public License v3.0** ([LICENSE](LICENSE)). You may use,
study, modify and share it. If you distribute it, or run a modified version as
a network service, you must release your complete source code under the same
licence.

For use under different terms (for example in a closed-source commercial
product), contact the author about a commercial licence.

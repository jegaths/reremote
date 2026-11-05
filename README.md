# rc-capture

Capture the RF codes of spare remotes with an Arduino Nano or ESP32-S3 and a
433 MHz receiver, give every button a name, and keep them in a SQLite library. The
library is the first step towards mapping an old fan remote onto a Home
Assistant smart switch.

```
remote ──433 MHz──> RW-54 receiver ──DAT──> Nano or ESP32-S3 ──USB serial──> Python server ──> browser
                                             (edge timing)                   (decode, group, SQLite)
```

## Quick start

```sh
make setup     # once: Python env, arduino-cli, Nano + ESP32 board cores
make flash     # plug a board in first; detects the port and board type
make run       # backend + UI, opens http://127.0.0.1:8000
```

`make sim` runs the UI with a fake board (its presses are shown but never
stored in the library), `make test` runs the tests,
`make ports` shows what is plugged in, `make help` lists everything. Override
detection with `make flash BOARD=esp32s3|nano|nano-old PORT=/dev/cu.xxx`.

## Step by step (first time, ESP32-S3)

Flashing needs only the USB cable, so do that first and wire the receiver
afterwards. The one rule: unplug USB before touching any jumper.

1. **Plug the bare board into the laptop** with a USB-C data cable (some
   cables are charge-only and will not show a port). Either of the two sockets
   on the S3 works.
2. **Install the tooling** (once):
   ```sh
   cd /Users/jegaths/Documents/Work/Personal/rc-capture
   make setup
   ```
3. **Check the board is seen**:
   ```sh
   make ports
   ```
   You should see a `/dev/cu.usbmodem…` or `/dev/cu.usbserial…` line and
   `detected: … esp32s3`. If the list is empty, try the other USB socket or a
   different cable.
4. **Flash the firmware**:
   ```sh
   make flash
   ```
   If it ends with "Failed to connect", hold **BOOT**, tap **RESET**, release
   BOOT, run `make flash` again. If the LED is still cycling colours after the
   flash or the log says `waiting for download`, unplug and replug the cable.
5. **Optional sanity check**: `make run`, pick the port, press Connect. The
   status bar should show `Connected: /dev/cu.… · ESP32-S3` and a firmware
   line. That proves board, cable and firmware are fine. Ctrl-C to stop.
6. **Unplug USB, then wire the receiver.** Push the ESP32-S3 and the receiver
   into the breadboard at opposite ends. Jumpers: receiver VCC to **3V3**,
   one receiver GND to GND, DAT to **GPIO 4**. Push a straight 17.3 cm wire
   into the ANT row. Leave NC and the duplicate VCC/GND pins empty.
   Double-check that nothing bridges 3V3 and GND.
7. **Plug USB back in and start the app**:
   ```sh
   make run
   ```
   The browser opens at http://127.0.0.1:8000. Pick the port, press
   **Connect**. The frame counter in the status bar will tick now and then
   from receiver noise; that is normal.
8. **Capture.** Hold the remote about a metre away and press a button for
   half a second. A card appears in *Live captures*. Choose **+ New remote…**
   in the card's dropdown, name it (e.g. "Crompton fan"), type the button name
   (e.g. "Power"), press **Save**.
9. **Press the same button again.** The new card should carry a green label
   with the remote and button name instead of a save form. That proves the
   code is fixed and the capture is good. If each press shows a different
   code, the remote uses rolling codes; note that before going further.
10. **Repeat step 8** for every button, then back up with **Export JSON** in
    the Library panel or `make export` while the server is running.

Stop the server with Ctrl-C. Next time, only steps 7 and 8 are needed; the
firmware stays on the board and the library stays in `data/remotes.sqlite`.

Same steps for the Arduino Nano, with receiver VCC on 5V and DAT on D2.

If the frame counter never moves even with the remote touching the antenna,
the remote is most likely transmitting on 2.4 GHz and this receiver cannot
hear it. If the counter moves but no cards appear, move the antenna away from
the laptop and USB cable, and try `min repeats` = 1 to see the raw frames.

## Hardware

Either board works; the server does not care which one is plugged in. The
firmware announces itself (`board=nano` or `board=esp32s3`) and the UI shows
it next to the port name.

### Option A: Arduino Nano

| Receiver pin | Nano pin |
|--------------|----------|
| VCC          | 5V       |
| GND          | GND      |
| DAT          | D2       |
| ANT          | 17.3 cm straight wire |

### Option B: ESP32-S3

| Receiver pin | ESP32-S3 pin |
|--------------|--------------|
| VCC          | **3V3** (not 5V / VBUS, see below) |
| GND          | GND          |
| DAT          | GPIO 4       |
| ANT          | 17.3 cm straight wire |

The ESP32-S3 runs 3.3 V logic and its GPIOs are not 5 V tolerant. Powering the
receiver from 3V3 keeps the DAT line at 3.3 V. If range turns out to be poor at
3.3 V, power the receiver from 5V and put a divider on DAT (2.2 kΩ from DAT to
GPIO 4, 3.3 kΩ from GPIO 4 to GND). To use another GPIO change `RX_PIN` in the
sketch; avoid the strapping pins 0, 3, 45, 46 and the USB pins 19, 20.

Eight-pin receivers (RXB6 / RXB12 layout: `GND DAT NC VCC` on one side,
`VCC GND GND ANT` on the other) have duplicated supply pins; connect one VCC,
one GND, DAT and ANT, leave the rest empty.

The antenna matters more than anything else. Without it the receiver only sees
the remote within a few centimetres and reports mostly noise.

## Firmware (`firmware/rf_capture/`)

One sketch, plain Arduino core, no libraries. An interrupt on the data pin
timestamps every edge and the loop groups the durations into frames separated
by silences longer than 4 ms. Each frame is sent as a text line:

```
F <millis> <n> <d1> <d2> ... <dn>
```

`d1` is the first HIGH pulse after the silence, durations alternate HIGH/LOW,
and `dn` is the silence that ended the frame. Serial commands: `?` identify,
`G <us>` separation gap, `P <us>` minimum pulse, `N <n>` minimum edge count.

### Flash the Arduino Nano

Arduino IDE: board "Arduino Nano", processor "ATmega328P" (or "ATmega328P (Old
Bootloader)" for most clones). Or:

```sh
arduino-cli compile --fqbn arduino:avr:nano firmware/rf_capture
arduino-cli upload  --fqbn arduino:avr:nano -p /dev/cu.usbserial-XXXX firmware/rf_capture
# clone with the old bootloader:
arduino-cli upload  --fqbn arduino:avr:nano:cpu=atmega328old -p /dev/cu.usbserial-XXXX firmware/rf_capture
```

### Flash the ESP32-S3

Most S3 dev boards have two USB-C sockets, one on the native USB peripheral
(often labelled "USB") and one through a UART bridge chip ("COM" or "UART").
The sketch mirrors its output to both, so either socket works for capturing.
Flashing works through either too.

Arduino IDE: install the "esp32 by Espressif" board package, choose
"ESP32S3 Dev Module", set **USB CDC On Boot: Enabled**, pick the port, Upload.
Or:

```sh
arduino-cli core install esp32:esp32   # once, after adding the Espressif board URL
arduino-cli compile --fqbn esp32:esp32:esp32s3:CDCOnBoot=cdc firmware/rf_capture
arduino-cli upload  --fqbn esp32:esp32:esp32s3:CDCOnBoot=cdc -p /dev/cu.usbmodemXXXX firmware/rf_capture
```

**Use the native USB socket** (the port shows up as `/dev/cu.usbmodem…`,
"ESP32-S3 native USB" in the UI). On the board tested here the UART-bridge
socket (`/dev/cu.usbserial…`, CP2102) never answered esptool at all, even in
manual download mode.

If the upload says "Failed to connect": hold BOOT, tap RESET, release BOOT,
upload again. If the serial log afterwards shows `boot:0x0 (DOWNLOAD...)` and
`waiting for download`, the chip is parked in download mode; esptool's reset
over native USB does not always get it out. Press RESET once, or simply
unplug and replug the USB cable. The LED colour cycle you see on a fresh board
is the factory demo and disappears once our firmware is running.

## Server (`server/`)

FastAPI + SQLite + pyserial, vanilla JS front-end.

```sh
uv venv .venv && uv pip install --python .venv/bin/python fastapi "uvicorn[standard]" pyserial
.venv/bin/python server/app.py                # http://127.0.0.1:8000
.venv/bin/python server/app.py --simulate     # fake Arduino, no hardware needed
```

Pick the Nano's serial port in the top bar and press Connect. Press a button on
the remote: the server waits until the same frame has been seen `min repeats`
times (default 2) and nothing new arrived for `settle ms`, then shows one
capture card. Single unrepeated frames are receiver noise and are dropped.

Each capture is decoded against the rc-switch protocol table (PT2262, EV1527,
HS1527, SC5262 and friends), so a known chip shows up as `rcswitch-<n>` with
the same protocol number, code and bit length that Tasmota, ESPHome and
OpenMQTTGateway would report. Anything else is kept as `RAW timings` with a
jitter-tolerant fingerprint and the averaged pulse train, which is enough to
replay later with a 433 MHz transmitter.

Choose a remote (or create one inline), type the button name, Save. A press
whose code is already in the library is labelled with its remote and button
name instead of offering a save form.

### Remotes that change the code on every press

Some remotes (the Crompton BLDC fan remote is one) keep a press counter inside
the code: the Crompton sends 32 bits where the top 20 are the remote address,
the next 4 are the button and the low byte is `15 × counter`. Exact matching
would never recognise a second press, so the library learns:

* Save the button once. Press it again, and in the new card's second dropdown
  pick **same as: &lt;button&gt;** instead of typing a name. That adds a sample.
* From two samples the matcher works out which bits stay constant (widened to
  whole nibbles, since counters live in fields) and ignores the rest. Other
  buttons of the same remote inherit that knowledge immediately, so one
  button's second press is enough for the whole remote.
* Recognised presses show a green badge with **≈** and are added as samples
  automatically, so the picture keeps sharpening. The *Samples* column in the
  library shows how many presses each button has learned from.

For replay later this matters: the fan will most likely ignore a frame whose
counter byte equals the previous one, so a transmitter must bump the counter.

The **Serial log** panel at the bottom shows every line from the board (boot
messages, `I` identification, `F` frames, `E` errors) and lets you send a
firmware command such as `?` or `G 3000`. Untick "frames" to see only the
non-frame lines.

The database lives in `data/remotes.sqlite` (override with `RC_DB=`).
`GET /api/export` downloads everything as JSON.

### API

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/ports` | serial ports |
| POST | `/api/connect` `{port, baud}` / `/api/disconnect` / `/api/simulate` | link control |
| POST | `/api/command` `{command}` | raw firmware command (`G 3000` ...) |
| POST | `/api/settings` `{min_repeats, settle_ms}` | grouping tunables |
| GET | `/api/events` | server-sent events: `status`, `frame`, `capture`, `error` |
| GET/POST/PATCH/DELETE | `/api/remotes[/{id}]` | remotes |
| GET/POST/PATCH/DELETE | `/api/buttons[/{id}]` | buttons (`POST` takes `capture_id` or raw `timings`) |
| GET/POST | `/api/buttons/{id}/samples` | presses a button has learned from; `POST` teaches another (`capture_id` or `timings`) |
| GET/DELETE | `/api/log` | serial log ring buffer (`?frames=false` hides frame lines) |
| GET/POST/DELETE | `/api/firmware[/{sketch}]` | sketches; files under `/api/firmware/{sketch}/files/{name}` (GET/PUT/DELETE) |
| POST | `/api/firmware/build` `{sketch, board, action, port}` | compile or flash; output streams as `build` SSE events |
| GET/PUT | `/api/ha/settings` | WiFi + Home Assistant settings (secrets never returned) |
| POST | `/api/ha/test`, GET `/api/ha/entities` | reach Home Assistant, list controllable entities |
| GET/PUT/DELETE | `/api/ha/actions[/{button_id}]`, POST `.../run` | button → service mappings, run one now |
| GET | `/api/ha/config.h` | the generated bridge config |
| GET/DELETE | `/api/captures` | raw capture log |
| GET | `/api/export` | full JSON dump |
| GET | `/api/bridge/status` | probe the installed bridge over WiFi: online, version, ip, rssi, uptime, last press |
| POST | `/api/bridge/reboot` | restart the installed bridge |
| POST | `/api/firmware/build` with `"port": "ota:<host>"` | compile and push the binary to the bridge over WiFi |

### Tests

```sh
cd server && ../.venv/bin/python -m pytest -q tests
```

## Home Assistant bridge (flash from the UI)

Once the buttons are in the library, the **Home Assistant** tab turns the
ESP32-S3 into a standalone bridge: it listens to the remote, recognises the
buttons with the same masks the library learned, and calls Home Assistant
services over WiFi. The laptop is not needed after flashing.

1. **Home Assistant tab → Connection.** Enter the WiFi SSID and password, the
   Home Assistant URL (e.g. `http://homeassistant.local:8123`) and a
   long-lived access token (HA profile → Security → Long-lived access tokens).
   Save, then **Test Home Assistant** should report your HA name and version.
2. **Load entities** pulls your fans, lights, switches, scenes and scripts.
   For each button pick an entity and an action: turn on/off, toggle, set fan
   speed %, speed up/down, brightness %. Each row saves itself. **Run** calls
   the service from the server right away, so you can confirm the mapping
   before anything is flashed.
3. **Flash over USB** (Installed bridge card). The server regenerates
   `firmware/ha_bridge/config.h` from the settings and the mappings (WiFi
   password and token included, which is why that file is gitignored), compiles
   the `ha_bridge` sketch, frees the serial port, uploads, and reconnects. The
   bridge's log then appears in the Serial log panel: `W` WiFi status and IP,
   `R` every decoded frame, `M` matched presses with the HTTP status Home
   Assistant returned, `U` over-the-air update progress.
4. **Update over WiFi** from then on. Once installed somewhere out of reach the
   bridge needs no cable: it answers on the LAN as `rc-bridge.local` (mDNS) and
   the *Installed bridge* card shows whether it is online, its firmware version,
   IP, WiFi signal, uptime and last press. **Update over WiFi** compiles the
   sketch with the current settings and mappings and pushes the binary to the
   bridge, which writes it to the spare OTA partition, verifies it and reboots;
   the card and the build output report when it is back. **Reboot** restarts it.
   The same WiFi target appears in the Firmware tab's port list, so any change
   to the sketch itself can be pushed the same way.

   The update endpoint is protected by a random token the server generates once
   and compiles into every bridge build (`OTA_TOKEN` in `config.h`; the token
   never leaves the local database otherwise). Because of that the *first*
   build with over-the-air support (firmware 1.1.0 or newer) must go over USB;
   every later one can go over WiFi. If `rc-bridge.local` does not resolve on
   your network, put the bridge's IP (shown in the Serial log as `W connected,
   ip …`) into **Bridge address**. The bridge keeps listening to the remote and
   calling Home Assistant while it is being updated; only the reboot (about
   three seconds) interrupts it.

**"cannot reach … No route to host" on macOS** although the browser opens the
same URL: macOS blocks local-network access for apps that have not been
granted *Local Network* permission, and reports it as "No route to host".
Start the server from your own Terminal with `make run` and click **Allow**
when macOS asks whether Python may find devices on the local network. If the
prompt never appeared, enable it by hand under System Settings → Privacy &
Security → Local Network for your terminal app, then restart `make run`.
Servers started from a background process cannot receive the prompt and stay
blocked. The ESP32 is unaffected: it talks to Home Assistant over WiFi itself.

On the board, a held button repeats its code; frames of the same button that
arrive less than 500 ms apart belong to one press, whatever the counter byte
does, so a press fires exactly one Home Assistant call even if that call takes
a while. Buttons with learned masks match on their stable bits, so the Crompton
counter byte is ignored exactly as in the UI. Prefer the plain `http://…:8123`
URL of Home Assistant over `https://`: the TLS handshake on the ESP32 adds
close to a second to the first call.

To capture more buttons later, flash `rf_capture` back from the Firmware tab,
capture, then flash `ha_bridge` again. Raw-timing buttons (not decodable by
rc-switch) are left out of the bridge; only rc-switch protocols run on the board.

## Firmware tab

Every folder under `firmware/` with a `.ino` of the same name is a sketch.
The Firmware tab lists them, opens their files in an editor (Cmd/Ctrl-S
saves), creates new sketches (empty or a copy of `rf_capture`), and compiles or
flashes to the detected port and board with arduino-cli's output streamed into
the page. A `sketch.json` manifest sets the title, description, allowed boards
and required libraries, which are installed before the build. Files listed
under `generated` (the bridge's `config.h`) are rewritten before each build and
shown read-only.

## Things to know before trusting a capture

* **Frequency.** The RW-54 is a 433.92 MHz ASK/OOK receiver. If the Crompton
  remote's RF side turns out to be 2.4 GHz (several BLDC fan remotes are), the
  receiver will show only noise no matter how close the remote is. Check the
  label inside the remote's battery compartment or the chip on its PCB
  (SYN115/SYN480/EV1527/HS1527 = 433 MHz; nRF24/A7105/XN297 = 2.4 GHz).
* **Rolling codes.** Press the same button three times. If the library labels
  the second and third press with the name you saved, the code is fixed and
  replayable. If each press is different, the remote uses a rolling/counter
  scheme and replay will need protocol work.
* **Superregenerative receivers are noisy.** The frame counter in the status
  bar ticking without any press is normal. Raise `min repeats` to 3 if
  nonsense raw captures appear.

## Layout

```
firmware/rf_capture/                 capture sketch for Nano and ESP32-S3
firmware/ha_bridge/                  ESP32-S3 sketch: remote -> Home Assistant over WiFi
server/app.py                        FastAPI app + CLI entry point
server/capture.py                    serial thread, burst grouping, SSE hub
server/decoder.py                    rc-switch protocol table, raw fingerprint
server/matcher.py                    sample-based button recognition (stable-bit masks)
server/firmware.py                   sketch files, arduino-cli build/flash runner
server/ha.py                         Home Assistant REST client, config.h generator
server/db.py                         SQLite schema and queries
server/simulator.py                  fake Arduino for development
server/static/                       index.html, style.css, app.js, firmware.js, ha.js
server/tests/                        pytest suite
```

## Next steps

1. Flash, connect, capture the Crompton remote, save its buttons.
2. Map buttons on the Home Assistant tab and flash the bridge.
3. Optional: a 433 MHz transmitter and a replay endpoint, so Home Assistant can
   also drive the fan's own receiver (the counter byte must change per send).

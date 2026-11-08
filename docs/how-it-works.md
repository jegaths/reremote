# How it works

## Capture firmware (`firmware/rf_capture/`)

One sketch for both boards, using the plain Arduino core with no libraries.
An interrupt on the data pin timestamps every edge. The loop groups the
durations into frames separated by silences longer than 4 ms, and sends each
frame as a text line:

```
F <millis> <n> <d1> <d2> ... <dn>
```

`d1` is the first HIGH pulse after the silence. Durations alternate HIGH and
LOW, and `dn` is the silence that ended the frame. Serial commands:
`?` identify, `G <us>` separation gap, `P <us>` minimum pulse,
`N <n>` minimum edge count.

## Grouping and decoding (`server/`)

The server waits until the same frame has been seen `min repeats` times
(default 2) and nothing new has arrived for `settle ms`. Then it shows one
capture card. Single frames that aren't repeated are receiver noise and are
dropped.

Each capture is decoded against the rc-switch protocol table (PT2262, EV1527,
HS1527, SC5262 and similar). A known chip shows up as `rcswitch-<n>` with the
same protocol number, code and bit length that Tasmota, ESPHome and
OpenMQTTGateway would report. Anything else is kept as `RAW timings`, with a
jitter-tolerant fingerprint and the averaged pulse train. That's enough to
replay it later with a 433 MHz transmitter.

A press whose code is already in the library is labelled with its remote and
button name instead of showing a save form.

## Remotes that change the code on every press

Some remotes keep a press counter inside the code. One BLDC ceiling-fan remote
tested with this project sends 32 bits: the top 20 are the remote address, the
next 4 are the button, and the low byte is `15 × counter`. Exact matching
would never recognise a second press, so the library learns instead:

* Save the button once. Press it again, and in the new card's second dropdown
  pick **same as: &lt;button&gt;** instead of typing a name. That adds a
  sample.
* From two samples, the matcher works out which bits stay constant (widened to
  whole nibbles, since counters live in fields) and ignores the rest. Other
  buttons of the same remote inherit this immediately, so a second press of
  one button is enough for the whole remote.
* Recognised presses show a green badge with **≈** and are added as samples
  automatically, so the matching keeps improving. The *Samples* column in the
  library shows how many presses each button has learned from.

For replay this matters: the device will most likely ignore a frame whose
counter byte equals the previous one, so a transmitter must increment the
counter.

The **Serial log** panel shows every line from the board (boot messages,
`I` identification, `F` frames, `E` errors) and lets you send a firmware
command such as `?` or `G 3000`. Untick "frames" to see only the non-frame
lines.

## Firmware tab

Every folder under `firmware/` that contains a `.ino` file with the same name
is a sketch. The Firmware tab lists the sketches and opens their files in an
editor (Cmd/Ctrl-S saves). It can create new sketches (empty, or a copy of
`rf_capture`) and compile or flash them to the detected port and board, with
arduino-cli's output streamed into the page. A `sketch.json` manifest sets the
title, description, allowed boards and required libraries; the libraries are
installed before the build. Files listed under `generated` (the bridge's
`config.h`) are rewritten before each build and shown read-only.

## Data

The database is `data/remotes.sqlite` (override it with the `REREMOTE_DB`
environment variable). **Export JSON** in the Library panel, `make export`, or
`GET /api/export` downloads everything as JSON.

## HTTP API

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/ports` | serial ports |
| POST | `/api/connect` `{port, baud}` / `/api/disconnect` / `/api/simulate` | link control |
| POST | `/api/command` `{command}` | raw firmware command (`G 3000` ...) |
| POST | `/api/settings` `{min_repeats, settle_ms}` | grouping settings |
| GET | `/api/events` | server-sent events: `status`, `frame`, `capture`, `error` |
| GET/POST/PATCH/DELETE | `/api/remotes[/{id}]` | remotes |
| GET/POST/PATCH/DELETE | `/api/buttons[/{id}]` | buttons (`POST` takes `capture_id` or raw `timings`) |
| GET/POST | `/api/buttons/{id}/samples` | presses a button has learned from; `POST` teaches another (`capture_id` or `timings`) |
| GET/DELETE | `/api/log` | serial log ring buffer (`?frames=false` hides frame lines) |
| GET/POST/DELETE | `/api/firmware[/{sketch}]` | sketches; files under `/api/firmware/{sketch}/files/{name}` (GET/PUT/DELETE) |
| POST | `/api/firmware/build` `{sketch, board, action, port}` | compile or flash; output streams as `build` SSE events; `"port": "ota:<host>"` pushes over WiFi |
| GET/PUT | `/api/ha/settings` | WiFi + Home Assistant settings (secrets never returned) |
| POST | `/api/ha/test`, GET `/api/ha/entities` | reach Home Assistant, list controllable entities |
| GET/PUT/DELETE | `/api/ha/actions[/{button_id}]`, POST `.../run` | button → service mappings, run one now |
| GET | `/api/ha/config.h` | the generated bridge config |
| GET/DELETE | `/api/captures` | raw capture log |
| GET | `/api/export` | full JSON dump |
| GET | `/api/bridge/status` | probe the installed bridge over WiFi: online, version, IP, RSSI, uptime, last press |
| POST | `/api/bridge/reboot` | restart the installed bridge |

## Code layout

```
firmware/rf_capture/   capture sketch for Nano and ESP32-S3
firmware/ha_bridge/    ESP32-S3 sketch: remote -> Home Assistant over WiFi
server/app.py          FastAPI app + CLI entry point
server/capture.py      serial thread, burst grouping, SSE hub
server/decoder.py      rc-switch protocol table, raw fingerprint
server/matcher.py      sample-based button recognition (stable-bit masks)
server/firmware.py     sketch files, arduino-cli build/flash runner
server/ota.py          over-the-air upload to the bridge
server/ha.py           Home Assistant REST client, config.h generator
server/db.py           SQLite schema and queries
server/simulator.py    fake board for development
server/static/         index.html, style.css, app.js, firmware.js, ha.js
server/tests/          pytest suite
scripts/findport.py    serial port + board detection used by the Makefile
```

## Running without make

```sh
python3 -m venv .venv
.venv/bin/pip install -r server/requirements.txt
.venv/bin/python server/app.py                 # http://127.0.0.1:8000
.venv/bin/python server/app.py --simulate      # fake board, no hardware
```

## Tests

```sh
make test
```

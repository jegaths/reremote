# Home Assistant bridge

Once the buttons are in the library, the **Home Assistant** tab turns the
ESP32-S3 into a standalone bridge. It listens to the remote, recognises the
buttons using the same masks the library learned, and calls Home Assistant
services over WiFi. The computer isn't needed after flashing.

## Before you start

- A running Home Assistant reachable from your network, plus an admin account
  to create a long-lived access token.
- The devices you want to control are **already integrated in Home
  Assistant** (e.g. a smart switch on the fan's power line, a smart bulb, or a
  scene). ReRemote only maps remote buttons to actions on existing entities.
  It doesn't add or pair devices.
- An ESP32-S3 (the Nano can't run the WiFi bridge) and 2.4 GHz WiFi.
- The remote's buttons already captured and saved in the library.

1. **Home Assistant tab → Connection.** Enter the WiFi SSID and password, the
   Home Assistant URL (e.g. `http://homeassistant.local:8123`), and a
   long-lived access token (in HA: your profile → Security → Long-lived access
   tokens). Save. **Test Home Assistant** should then report your HA name and
   version.
2. **Load entities** pulls in your fans, lights, switches, scenes and scripts.
   For each button, pick an entity and an action: turn on/off, toggle, set fan
   speed %, speed up/down, or brightness %. Each row saves itself. **Run**
   calls the service from the server straight away, so you can check the
   mapping before anything is flashed.
3. **Flash over USB** (*Installed bridge* card). The server regenerates
   `firmware/ha_bridge/config.h` from the settings and mappings. That file
   includes the WiFi password and token, which is why it is gitignored. The
   server then compiles the `ha_bridge` sketch, frees the serial port, uploads,
   and reconnects. The bridge's log appears in the Serial log panel:
   `W` WiFi status and IP, `R` every decoded frame, `M` matched presses with
   the HTTP status Home Assistant returned, and `U` over-the-air update
   progress.
4. **Update over WiFi** from then on. The bridge needs no cable once it is
   installed. It answers on the LAN as `reremote-bridge.local` (mDNS), and the
   *Installed bridge* card shows whether it is online, plus its firmware
   version, IP, WiFi signal, uptime and last press. **Update over WiFi**
   compiles the sketch with the current settings and mappings and pushes the
   binary to the bridge. The bridge writes it to the spare OTA partition,
   verifies it and reboots. **Reboot** restarts it. The same WiFi target
   appears in the Firmware tab's port list, so changes to the sketch itself
   can be pushed the same way.

## Security notes

- The update endpoint is protected by a random token. The server generates it
  once and compiles it into every bridge build (`OTA_TOKEN` in `config.h`).
  Because of that, the *first* build with over-the-air support has to go over
  USB. Every later build can go over WiFi.
- WiFi credentials and the Home Assistant token are stored in the local SQLite
  database (`data/`) and in the generated `config.h`. Both are gitignored.
  The API never returns them.
- The server listens on `127.0.0.1` by default. If you start it with
  `HOST=0.0.0.0`, anyone on your network can reach it. It has no login.

## Tips

- If `reremote-bridge.local` doesn't resolve on your network, enter the bridge's IP
  (shown in the Serial log as `W connected, ip …`) in **Bridge address**.
- The bridge keeps listening and calling Home Assistant while it is being
  updated. Only the reboot (about three seconds) interrupts it.
- A held button repeats its code. Frames of the same button that arrive less
  than 500 ms apart count as one press, so a press triggers exactly one Home
  Assistant call.
- Prefer a plain `http://…:8123` URL over `https://`. The TLS handshake on the
  ESP32 adds close to a second to the first call.
- To capture more buttons later, flash `rf_capture` back from the Firmware
  tab, capture, then flash `ha_bridge` again.
- Raw-timing buttons (codes rc-switch can't decode) are left out of the
  bridge. Only rc-switch protocols run on the board.

## "cannot reach … No route to host" on macOS

This can happen even though the browser opens the same URL. macOS blocks
local-network access for apps that haven't been granted *Local Network*
permission, and reports it as "No route to host". Start the server from your
own terminal with `make run` and click **Allow** when macOS asks. If the
prompt never appeared, enable it by hand under System Settings → Privacy &
Security → Local Network for your terminal app, then restart `make run`.
Servers started from a background process can't receive the prompt and stay
blocked. The ESP32 isn't affected, because it talks to Home Assistant over
WiFi itself.

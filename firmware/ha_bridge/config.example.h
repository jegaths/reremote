// Example of the file the rc-capture server generates as config.h before each
// build of this sketch (Home Assistant tab -> settings + button actions).
// config.h itself is gitignored because it holds your WiFi password and token.
#pragma once
#include <stdint.h>

#define WIFI_SSID "my-wifi"
#define WIFI_PASS "my-password"
#define HA_URL    "http://homeassistant.local:8123"
#define HA_TOKEN  "eyJ...long-lived-access-token..."
#define RX_PIN    4

struct ButtonMap {
  uint8_t     protocol;   // rc-switch protocol number
  uint8_t     bits;
  uint32_t    code;       // reference code of the button
  uint32_t    mask;       // bits that identify the button (counter bits cleared)
  const char *name;
  const char *path;       // Home Assistant service path, "" for no action
  const char *body;       // JSON body for the service call
};

static const ButtonMap BUTTONS[] = {
  {1, 32, 0x011C0E69, 0xFFFFFF00, "On",  "/api/services/fan/turn_on",  "{\"entity_id\":\"fan.bedroom\"}"},
  {1, 32, 0x011C0678, 0xFFFFFF00, "Off", "/api/services/fan/turn_off", "{\"entity_id\":\"fan.bedroom\"}"},
};

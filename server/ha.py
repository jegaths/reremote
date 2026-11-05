"""Home Assistant REST client and ha_bridge config.h generation."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Optional

from matcher import button_masks

ACTION_DOMAINS = ("fan", "light", "switch", "scene", "script", "input_boolean", "automation")

# What the UI offers per entity domain: (label, service, extra fields)
ACTIONS: dict[str, list[dict[str, Any]]] = {
    "fan": [
        {"label": "Turn on", "service": "turn_on"},
        {"label": "Turn off", "service": "turn_off"},
        {"label": "Toggle", "service": "toggle"},
        {"label": "Set speed %", "service": "set_percentage", "field": "percentage"},
        {"label": "Speed up", "service": "increase_speed"},
        {"label": "Speed down", "service": "decrease_speed"},
    ],
    "light": [
        {"label": "Turn on", "service": "turn_on"},
        {"label": "Turn off", "service": "turn_off"},
        {"label": "Toggle", "service": "toggle"},
        {"label": "Brightness %", "service": "turn_on", "field": "brightness_pct"},
    ],
    "switch": [
        {"label": "Turn on", "service": "turn_on"},
        {"label": "Turn off", "service": "turn_off"},
        {"label": "Toggle", "service": "toggle"},
    ],
    "scene": [{"label": "Activate", "service": "turn_on"}],
    "script": [{"label": "Run", "service": "turn_on"}],
    "input_boolean": [
        {"label": "Turn on", "service": "turn_on"},
        {"label": "Turn off", "service": "turn_off"},
        {"label": "Toggle", "service": "toggle"},
    ],
    "automation": [{"label": "Trigger", "service": "trigger"}],
}


class HomeAssistant:
    def __init__(self, url: str, token: str, timeout: float = 6.0):
        self.url = (url or "").rstrip("/")
        self.token = token or ""
        self.timeout = timeout

    def _req(self, path: str, body: Optional[dict[str, Any]] = None) -> Any:
        if not self.url or not self.token:
            raise RuntimeError("Home Assistant URL and token are not set")
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.url + path, data=data, method="POST" if data else "GET",
                                     headers={"Authorization": f"Bearer {self.token}",
                                              "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                raw = r.read().decode()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:200]
            if e.code == 401:
                raise RuntimeError("Home Assistant rejected the token (401)") from e
            raise RuntimeError(f"Home Assistant replied {e.code}: {detail}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"cannot reach {self.url}: {e.reason}") from e

    def ping(self) -> dict[str, Any]:
        api = self._req("/api/")
        cfg = self._req("/api/config")
        return {"message": api.get("message"), "location": cfg.get("location_name"),
                "version": cfg.get("version")}

    def entities(self) -> list[dict[str, Any]]:
        out = []
        for st in self._req("/api/states"):
            eid = st["entity_id"]
            domain = eid.split(".", 1)[0]
            if domain not in ACTION_DOMAINS:
                continue
            attrs = st.get("attributes", {})
            out.append({"entity_id": eid, "domain": domain,
                        "name": attrs.get("friendly_name", eid), "state": st.get("state"),
                        "percentage": attrs.get("percentage"),
                        "supports_speed": domain == "fan" and bool(attrs.get("supported_features", 0) & 1)})
        out.sort(key=lambda e: (ACTION_DOMAINS.index(e["domain"]), e["name"].lower()))
        return out

    def call(self, domain: str, service: str, data: dict[str, Any]) -> Any:
        if domain not in ACTION_DOMAINS:
            raise RuntimeError(f"domain {domain} not allowed")
        return self._req(f"/api/services/{domain}/{service}", data)


# ---- config.h generation -------------------------------------------------------
def _c_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def action_to_call(action: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """(service path, JSON body) for a stored button action."""
    data = {"entity_id": action["entity_id"]}
    extra = action.get("data") or {}
    data.update(extra)
    return f"/api/services/{action['domain']}/{action['service']}", data


def generate_config_h(settings: dict[str, str], buttons: list[dict[str, Any]],
                      actions: dict[int, dict[str, Any]]) -> str:
    """buttons from Database.buttons_with_samples(); actions keyed by button id."""
    masks = button_masks(buttons)
    rows = []
    for b in buttons:
        if not b["protocol"].startswith("rcswitch-") or b["code"] is None:
            continue   # raw-timing buttons cannot be matched by rc-switch on the board
        proto = int(b["protocol"].split("-")[1])
        bits = b["bit_length"]
        mask = masks.get(b["id"], (1 << bits) - 1)
        act = actions.get(b["id"])
        if act:
            path, body = action_to_call(act)
        else:
            path, body = "", {}
        label = f"{b.get('remote_name', '')} / {b['name']}".strip(" /")
        rows.append(f"  {{{proto}, {bits}, 0x{b['code']:08X}, 0x{mask:08X}, {_c_str(label)}, "
                    f"{_c_str(path)}, {_c_str(json.dumps(body, separators=(',', ':')))}}},")
    pin = settings.get("rx_pin", "4") or "4"
    return "\n".join([
        "// Generated by rc-capture from the library and the Home Assistant tab.",
        "// Do not edit: it is rewritten before every build of ha_bridge.",
        "#pragma once",
        "#include <stdint.h>",
        "",
        f"#define WIFI_SSID {_c_str(settings.get('wifi_ssid', ''))}",
        f"#define WIFI_PASS {_c_str(settings.get('wifi_pass', ''))}",
        f"#define HA_URL    {_c_str((settings.get('ha_url', '') or '').rstrip('/'))}",
        f"#define HA_TOKEN  {_c_str(settings.get('ha_token', ''))}",
        f"#define RX_PIN    {int(pin)}",
        "",
        "struct ButtonMap {",
        "  uint8_t     protocol;",
        "  uint8_t     bits;",
        "  uint32_t    code;",
        "  uint32_t    mask;",
        "  const char *name;",
        "  const char *path;",
        "  const char *body;",
        "};",
        "",
        "static const ButtonMap BUTTONS[] = {",
        *(rows or ["  {0, 0, 0, 0, \"\", \"\", \"{}\"},  // no buttons in the library yet"]),
        "};",
        "",
    ])

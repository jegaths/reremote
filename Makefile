# ReRemote — one entry point for everything.
#
#   make setup          install Python deps, arduino-cli and both board cores (idempotent)
#   make flash          compile + upload the firmware to whatever board is plugged in
#   make run            start the web UI + backend at http://127.0.0.1:8000
#   make sim            same, with the fake Arduino (no hardware)
#   make test           run the test suite
#
# Overrides:  make flash BOARD=esp32s3|esp32|esp32s2|esp32c3|esp32c6|nano|nano-old PORT=/dev/cu.usbmodem1101   (Linux: /dev/ttyACM0)
#             make run HOST=0.0.0.0 PORT_HTTP=8080
#
# Works on macOS and Linux. Needs Python 3.10+; uses uv when installed, plain
# venv + pip otherwise. arduino-cli is taken from PATH, or downloaded into
# .tools/bin (inside this folder) when missing.

SHELL := /bin/bash
.DEFAULT_GOAL := help

VENV      := .venv
PY        := $(VENV)/bin/python
UV        := $(shell command -v uv 2>/dev/null)
PYTHON3   ?= python3
TOOLS_BIN := $(CURDIR)/.tools/bin
export PATH := $(TOOLS_BIN):$(PATH)
UNAME     := $(shell uname -s)
ACLI_VERSION := 1.5.1
ACLI_DOCS    := https://arduino.github.io/arduino-cli/latest/installation/
# the local copy wins; recursive so it sees a copy downloaded by setup-arduino
ACLI          = $(firstword $(wildcard $(TOOLS_BIN)/arduino-cli) arduino-cli)
ESP32_URL := https://espressif.github.io/arduino-esp32/package_esp32_index.json
HOST      ?= 127.0.0.1
PORT_HTTP ?= 8000
URL       := http://$(HOST):$(PORT_HTTP)

SKETCH    ?= firmware/rf_capture      # or: make flash SKETCH=firmware/ha_bridge
FQBN_esp32s3  := esp32:esp32:esp32s3:CDCOnBoot=cdc
FQBN_esp32    := esp32:esp32:esp32
FQBN_esp32s2  := esp32:esp32:esp32s2:CDCOnBoot=cdc
FQBN_esp32c3  := esp32:esp32:esp32c3:CDCOnBoot=cdc
FQBN_esp32c6  := esp32:esp32:esp32c6:CDCOnBoot=cdc
FQBN_nano     := arduino:avr:nano
FQBN_nano-old := arduino:avr:nano:cpu=atmega328old

# --------------------------------------------------------------------------- help
.PHONY: help
help:
	@echo ""
	@echo "ReRemote"
	@echo "  make setup        install everything (python env, arduino-cli, Nano + ESP32 cores)"
	@echo "  make flash        compile + upload firmware (SKETCH=firmware/ha_bridge for the bridge)"
	@echo "  make run          start backend + UI at $(URL)"
	@echo "  make sim          start with the simulator (no hardware needed)"
	@echo "  make test         run tests"
	@echo "  make ports        list serial ports and which board they look like"
	@echo "  make compile      compile firmware for both boards without uploading"
	@echo "  make monitor      raw serial monitor on the attached board"
	@echo "  make export       dump the library to remotes-export.json"
	@echo "  make clean        remove build output, caches and the venv"
	@echo ""
	@echo "Overrides: BOARD=esp32s3|esp32|esp32s2|esp32c3|esp32c6|nano|nano-old  PORT=/dev/cu.xxx|/dev/ttyACM0  HOST=  PORT_HTTP="
	@echo ""

# --------------------------------------------------------------------------- setup
.PHONY: setup setup-python setup-arduino
setup: setup-python setup-arduino
	@echo ""
	@echo "✔ setup complete.  Next: plug in the board, then 'make flash' and 'make run'."

setup-python: $(VENV)/.ok
$(VENV)/.ok: server/requirements.txt
ifdef UV
	@test -x $(PY) || uv venv $(VENV) -q
	@uv pip install -q --python $(PY) -r server/requirements.txt
else
	@$(PYTHON3) -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null \
	  || { echo "Python 3.10 or newer is needed (or install uv: https://docs.astral.sh/uv/)"; exit 1; }
	@test -x $(PY) || $(PYTHON3) -m venv $(VENV) \
	  || { echo "could not create a venv; on Debian/Ubuntu: sudo apt install python3-venv"; exit 1; }
	@$(PY) -m pip install -q --upgrade pip
	@$(PY) -m pip install -q -r server/requirements.txt
endif
	@touch $@
	@echo "✔ python deps installed in $(VENV)"

setup-arduino:
	@if ! command -v arduino-cli >/dev/null; then \
	  case "$(UNAME)" in Darwin) os=macOS;; Linux) os=Linux;; *) os=;; esac; \
	  case "$$(uname -m)" in x86_64|amd64) arch=64bit;; arm64|aarch64) arch=ARM64;; armv7l) arch=ARMv7;; *) arch=;; esac; \
	  test -n "$$os" -a -n "$$arch" || { echo "no arduino-cli download for this system: install it by hand, see $(ACLI_DOCS)"; exit 1; }; \
	  echo "arduino-cli not found: downloading $(ACLI_VERSION) into .tools/bin"; \
	  mkdir -p $(TOOLS_BIN) && \
	  curl -fsSL https://downloads.arduino.cc/arduino-cli/arduino-cli_$(ACLI_VERSION)_$${os}_$${arch}.tar.gz \
	    | tar -xz -C $(TOOLS_BIN) arduino-cli \
	  || { echo "download failed: install arduino-cli by hand, see $(ACLI_DOCS)"; exit 1; }; \
	fi
	@$(ACLI) config init >/dev/null 2>&1 || true
	@$(ACLI) config get board_manager.additional_urls 2>/dev/null | grep -q espressif \
	  || $(ACLI) config add board_manager.additional_urls $(ESP32_URL) >/dev/null
	@$(ACLI) core update-index >/dev/null
	@$(ACLI) core list | grep -q '^arduino:avr' || $(ACLI) core install arduino:avr
	@$(ACLI) core list | grep -q '^esp32:esp32' || $(ACLI) core install esp32:esp32
	@$(ACLI) lib list 2>/dev/null | grep -q '^rc-switch' || $(ACLI) lib install rc-switch
	@echo "✔ arduino-cli ready with Nano and ESP32 cores and the rc-switch library"

# --------------------------------------------------------------------------- firmware
# Auto-detect the port and board unless given on the command line
PORT  ?= $(shell $(PY) scripts/findport.py port 2>/dev/null)
BOARD ?= $(shell $(PY) scripts/findport.py board 2>/dev/null)
FQBN   = $(FQBN_$(BOARD))

.PHONY: ports compile compile-nano compile-esp32s3 flash monitor
ports: setup-python
	@$(PY) scripts/findport.py list
	@echo "detected: $$($(PY) scripts/findport.py both 2>/dev/null || echo none)"
ifeq ($(UNAME),Linux)
	@id -nG | grep -qwE 'dialout|uucp' || echo "note: add yourself to the serial group, then log out and in: sudo usermod -aG dialout $$USER"
endif

compile: compile-nano compile-esp32s3
compile-nano:
	$(ACLI) compile --fqbn $(FQBN_nano) $(SKETCH)
compile-esp32s3:
	$(ACLI) compile --fqbn $(FQBN_esp32s3) $(SKETCH)

flash: setup-python
	@test -n "$(PORT)"  || { echo "no board detected. Plug it in, or pass PORT=/dev/cu.xxx (Linux: /dev/ttyACM0)"; exit 1; }
	@test -n "$(FQBN)"  || { echo "unknown board '$(BOARD)'. Pass BOARD=esp32s3, esp32, esp32s2, esp32c3, esp32c6, nano or nano-old"; exit 1; }
	@echo "→ flashing $(BOARD) on $(PORT)"
	$(ACLI) compile --fqbn $(FQBN) $(SKETCH)
	$(ACLI) upload  --fqbn $(FQBN) -p $(PORT) $(SKETCH)
	@echo "✔ flashed. Now: make run"

monitor:
	@test -n "$(PORT)" || { echo "no board detected"; exit 1; }
	$(ACLI) monitor -p $(PORT) -c baudrate=115200

# --------------------------------------------------------------------------- server
.PHONY: run sim test export
OPEN_UI = ( sleep 1.5; $(PY) -m webbrowser -t $(URL) >/dev/null 2>&1 || true ) &

run: setup-python
ifeq ($(UNAME),Darwin)
	@echo "If macOS asks whether Python may find devices on your local network, click Allow:"
	@echo "the Home Assistant tab needs it to reach HA on your LAN."
endif
	@$(OPEN_UI)
	$(PY) server/app.py --host $(HOST) --port $(PORT_HTTP)

sim: setup-python
	@$(OPEN_UI)
	$(PY) server/app.py --host $(HOST) --port $(PORT_HTTP) --simulate

test: setup-python
	cd server && ../$(PY) -m pytest -q tests

export:
	@curl -sf $(URL)/api/export -o remotes-export.json && echo "✔ wrote remotes-export.json" \
	  || echo "server not running: start it with 'make run' first"

# --------------------------------------------------------------------------- clean
.PHONY: clean
clean:
	rm -rf $(VENV) server/.pytest_cache server/__pycache__ server/tests/__pycache__ $(SKETCH)/build

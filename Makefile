# rc-capture — one entry point for everything.
#
#   make setup          install Python deps, arduino-cli and both board cores (idempotent)
#   make flash          compile + upload the firmware to whatever board is plugged in
#   make run            start the web UI + backend at http://127.0.0.1:8000
#   make sim            same, with the fake Arduino (no hardware)
#   make test           run the test suite
#
# Overrides:  make flash BOARD=esp32s3|nano|nano-old PORT=/dev/cu.usbmodem1101
#             make run HOST=0.0.0.0 PORT_HTTP=8080

SHELL := /bin/bash
.DEFAULT_GOAL := help

VENV      := .venv
PY        := $(VENV)/bin/python
UV        := $(shell command -v uv 2>/dev/null)
ARDUINO   := $(shell command -v arduino-cli 2>/dev/null)
SKETCH    := firmware/rf_capture
ESP32_URL := https://espressif.github.io/arduino-esp32/package_esp32_index.json
HOST      ?= 127.0.0.1
PORT_HTTP ?= 8000
URL       := http://$(HOST):$(PORT_HTTP)

FQBN_esp32s3  := esp32:esp32:esp32s3:CDCOnBoot=cdc
FQBN_nano     := arduino:avr:nano
FQBN_nano-old := arduino:avr:nano:cpu=atmega328old

# --------------------------------------------------------------------------- help
.PHONY: help
help:
	@echo ""
	@echo "rc-capture"
	@echo "  make setup        install everything (python env, arduino-cli, Nano + ESP32 cores)"
	@echo "  make flash        compile + upload firmware to the board that is plugged in"
	@echo "  make run          start backend + UI at $(URL)"
	@echo "  make sim          start with the simulator (no hardware needed)"
	@echo "  make test         run tests"
	@echo "  make ports        list serial ports and which board they look like"
	@echo "  make compile      compile firmware for both boards without uploading"
	@echo "  make monitor      raw serial monitor on the attached board"
	@echo "  make export       dump the library to remotes-export.json"
	@echo "  make clean        remove build output, caches and the venv"
	@echo ""
	@echo "Overrides: BOARD=esp32s3|nano|nano-old  PORT=/dev/cu.xxx  HOST=  PORT_HTTP="
	@echo ""

# --------------------------------------------------------------------------- setup
.PHONY: setup setup-python setup-arduino
setup: setup-python setup-arduino
	@echo ""
	@echo "✔ setup complete.  Next: plug in the board, then 'make flash' and 'make run'."

setup-python: $(VENV)/.ok
$(VENV)/.ok: server/requirements.txt
ifndef UV
	$(error uv not found. Install it with: curl -LsSf https://astral.sh/uv/install.sh | sh)
endif
	@test -x $(PY) || uv venv $(VENV) -q
	@uv pip install -q --python $(PY) -r server/requirements.txt
	@touch $@
	@echo "✔ python deps installed in $(VENV)"

setup-arduino:
	@if ! command -v arduino-cli >/dev/null; then \
	  if command -v brew >/dev/null; then echo "installing arduino-cli via brew"; brew install arduino-cli; \
	  else echo "arduino-cli not found and brew missing: see https://arduino.github.io/arduino-cli/latest/installation/"; exit 1; fi; \
	fi
	@arduino-cli config init --overwrite >/dev/null 2>&1 || true
	@arduino-cli config add board_manager.additional_urls $(ESP32_URL) >/dev/null 2>&1 || true
	@arduino-cli core update-index >/dev/null
	@arduino-cli core list | grep -q '^arduino:avr' || arduino-cli core install arduino:avr
	@arduino-cli core list | grep -q '^esp32:esp32' || arduino-cli core install esp32:esp32
	@echo "✔ arduino-cli ready with Nano and ESP32 cores"

# --------------------------------------------------------------------------- firmware
# Auto-detect the port and board unless given on the command line
PORT  ?= $(shell $(PY) scripts/findport.py port 2>/dev/null)
BOARD ?= $(shell $(PY) scripts/findport.py board 2>/dev/null)
FQBN   = $(FQBN_$(BOARD))

.PHONY: ports compile compile-nano compile-esp32s3 flash monitor
ports: setup-python
	@$(PY) scripts/findport.py list
	@echo "detected: $$($(PY) scripts/findport.py both 2>/dev/null || echo none)"

compile: compile-nano compile-esp32s3
compile-nano:
	arduino-cli compile --fqbn $(FQBN_nano) $(SKETCH)
compile-esp32s3:
	arduino-cli compile --fqbn $(FQBN_esp32s3) $(SKETCH)

flash: setup-python
	@test -n "$(PORT)"  || { echo "no board detected. Plug it in, or pass PORT=/dev/cu.xxx"; exit 1; }
	@test -n "$(FQBN)"  || { echo "unknown board '$(BOARD)'. Pass BOARD=esp32s3, nano or nano-old"; exit 1; }
	@echo "→ flashing $(BOARD) on $(PORT)"
	arduino-cli compile --fqbn $(FQBN) $(SKETCH)
	arduino-cli upload  --fqbn $(FQBN) -p $(PORT) $(SKETCH)
	@echo "✔ flashed. Now: make run"

monitor:
	@test -n "$(PORT)" || { echo "no board detected"; exit 1; }
	arduino-cli monitor -p $(PORT) -c baudrate=115200

# --------------------------------------------------------------------------- server
.PHONY: run sim test export
run: setup-python
	@( sleep 1.5; open $(URL) 2>/dev/null || true ) &
	$(PY) server/app.py --host $(HOST) --port $(PORT_HTTP)

sim: setup-python
	@( sleep 1.5; open $(URL) 2>/dev/null || true ) &
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

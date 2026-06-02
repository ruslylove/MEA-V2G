# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the Application

**Always run with `sudo` and via the venv** — raw socket and `/dev/mem` access require root:

```bash
# EVSE mode (MEA project default)
sudo .venv/bin/python3 Application.py eth_raw -i enp3s0 -r EVSE -ec evse.json

# EVSE mode with OCPP
sudo .venv/bin/python3 Application.py eth_raw -i enp3s0 -r EVSE -ec evse.json \
  --ocpp-id "rddQC4000001" \
  --ocpp-url "wss://ocpp.measandbox.com:2930/EV/Srv/JSON/1.6/rddQC4000001" \
  --ocpp-version 1.6

# EV mode
sudo .venv/bin/python3 Application.py eth -i enx00e09909a99b -r EV -c ev.json

# GUI launcher (handles sudo internally)
python3 gui_launcher.py
```

Interface types: `eth` (Scapy), `eth_raw` (raw Linux sockets, recommended for BeagleBone), `spi`, `spi_pru` (BeagleBone PRU offload).

## Running Tests

```bash
# All unit tests
.venv/bin/python3 -m pytest tests/unit/ -v

# Single test file
.venv/bin/python3 -m pytest tests/unit/test_evse_states.py -v

# Single test
.venv/bin/python3 -m pytest tests/unit/test_evse_states.py::TestEvseStates::test_initial_state -v

# Integration tests (require live OCPP connection to MEA sandbox)
.venv/bin/python3 -m pytest tests/system/ -v

# API tests (require live MEA CSMS)
.venv/bin/python3 -m pytest tests/api/ -v
```

Unit tests mock `Whitebeet` and hardware dependencies at import time — no hardware needed.

## Deploying to BeagleBone

```bash
./deploy_to_beaglebone.sh    # rsync to beaglebone.local
ssh debian@beaglebone.local
cd ~/MEA-V2G && sudo .venv/bin/python3 Application.py spi_pru -i spidev0.0 -m 00:01:01:63:77:33 -r EVSE
```

## Architecture Overview

The system implements an ISO 15118 EVSE (charging station) and EV (vehicle) in Python. There are two hardware backends: the **Whitebeet** module (current) and the **Vector vSECC** (migration target).

### Entry Point and Roles

`Application.py` parses CLI args and instantiates either `Evse` or `Ev`. Both classes own a `Whitebeet` instance and a `ChargerInterface` implementation.

### Layer Stack

```
Application.py
    └── Evse.py / Ev.py          ← State machine, session orchestration
            ├── Whitebeet.py     ← Hardware abstraction (CP, SLAC, V2G commands)
            │       └── FramingInterface.py  ← Binary framing protocol
            │               └── [SpiAdapter / EthernetAdapter / EthernetAdapterRaw / PruSpiAdapter]
            ├── ChargerInterface.py (ABC)
            │       ├── ChargerSim.py    ← Software-simulated power electronics
            │       └── CanCharger.py   ← Real Phoenix Contact charger via CAN (python-can)
            └── OcppWorker.py    ← Runs OCPP in a background thread
                    ├── Ocpp16Interface.py
                    ├── Ocpp201Interface.py
                    └── Ocpp21Interface.py
```

### Whitebeet Communication

`Whitebeet.py` sends binary commands to the module using module IDs (e.g., `0x29` = Control Pilot, `0x28` = SLAC, `0x27` = V2G) and sub-IDs. `FramingInterface` wraps these in frames and routes them over the selected transport adapter. The `_sendReceive` / `_sendReceiveAck` pattern is synchronous — it blocks until the module ACKs or times out.

### Evse Session Flow

`Evse._initialize()` → `_waitEvConnected()` → `_handleEvConnected()` (starts SLAC) → `_handleNetworkEstablished()` (configures V2G, calls `whitebeet.v2gEvseStartListen()`) → then a `run()` loop that polls `whitebeet.framing.receive_next_frame()` for V2G notifications (authorization requests, cable check, charging parameters, charge loop, stop).

The OCPP `OcppWorker` runs in a separate `threading.Thread` with its own `asyncio` event loop, communicating back to `Evse` via thread-safe methods on the worker.

### ChargerInterface

All charger implementations must implement `ChargerInterface` (ABC). `ChargerSim` ramps voltage/current linearly based on delta parameters from `evse.json`. `CanCharger` talks to a real Phoenix Contact power module over CAN at 125kbps using a proprietary frame format.

### vSECC (Migration Target)

The Vector vSECC.single Board (`192.168.1.166`) replaces the Whitebeet entirely. It handles CP/SLAC/ISO 15118 autonomously. Integration is via:
- **MQTT broker** at `192.168.1.166:1883` (user: `vector`, password: `vector`) — subscribe `vsecc/#` for events
- **PEP-WS** (WebSocket) for power electronics — vSECC connects to your server
- **REST API** at `http://192.168.1.166/api` (JWT auth: `POST /api/login` with `{"name":"admin","password":"admin"}`)

Key MQTT topics: `vsecc/connector/1/ev/cp_state`, `vsecc/connector/1/status/charging_session_state`, `vsecc/connector/1/status/charging_authorization_state`.

### Configuration Files

- `evse.json` — EVSE MAC address, charger limits (max voltage/current/power, delta ramp rates), and default charging schedule
- `ev.json` — EV MAC address and battery parameters

### PRU SPI Mode (BeagleBone only)

`PruUtils.py` loads `pru/spi_whitebeet.out` firmware into the BeagleBone PRU via `/sys/class/remoteproc/`. `PruSpiAdapter.py` communicates with the PRU through shared RAM at `0x4A310000` (mmap of `/dev/mem`). The PRU handles the SPI handshake at 200MHz to eliminate Linux scheduling jitter.

### MEA Sandbox OCPP

The MEA sandbox CSMS is at `wss://ocpp.measandbox.com:2930/EV/Srv/JSON/1.6/<CP_ID>`. API credentials are in `tests/system/conftest.py`. The `V2GMode` OCPP variable controls BPT (bidirectional power transfer) capability at the CSMS level.

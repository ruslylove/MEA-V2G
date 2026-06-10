# MEA OCPP 1.6 Compliance Test — Setup & Run Manual

Vector vSECC.single Board vs MEA CSMS Sandbox  
Sections 2–11 | Direct connection (no proxy)

---

## 1. System Overview

```
[EV]──plug──[vSECC.single Board]──OCPP/TLS──[MEA CSMS Sandbox]
                  │  192.168.1.166                wss://ocpp.measandbox.com:2930
                  │
              MQTT :1883
              REST  :80/api
                  │
             [Test PC]  192.168.1.200/24 (alias on enp3s0)
                  │     also 192.168.111.185/24 (DHCP)
                  │
              MEA REST API
              https://ocppapi.measandbox.com/EV   (Digest auth)
```

The vSECC handles SLAC, CP signalling, and ISO 15118 autonomously.  
The test PC observes MQTT events and issues commands via the MEA REST API.

---

## 2. Hardware Requirements

| Component | Details |
|-----------|---------|
| Vector vSECC.single Board | Firmware with OCPP 1.6 JSON, MQTT enabled |
| Test PC | Linux, Python 3.11+, `pdflatex` installed |
| Network switch | Connects vSECC + PC on `192.168.1.x` subnet |
| EV (optional) | Required for EV-plug items; all others run without EV |

The vSECC must be pre-configured with:
- OCPP endpoint: `wss://ocpp.measandbox.com:2930/EV/Srv/JSON/1.6/rddQC4000001`
- MQTT broker: `192.168.1.166:1883` (internal, already built-in)
- CP identity: `rddQC4000001`

---

## 3. Network Setup

### 3.1 Physical connections

```
PC  enp3s0  ──switch──  vSECC ETH port
```

The PC's `enp3s0` has a DHCP address on `192.168.111.0/24`.  
The vSECC is at `192.168.1.166`.  
Add an alias so the PC can reach the vSECC subnet:

```bash
sudo ip addr add 192.168.1.200/24 dev enp3s0
```

> **This alias is not persistent** — re-run it after every reboot or network restart.

### 3.2 Verify connectivity

```bash
ping -c 3 192.168.1.166          # vSECC should reply
curl http://192.168.1.166/api    # should return a JSON error (not timeout)
```

### 3.3 Make the alias persistent (optional)

Add to `/etc/network/interfaces` or a NetworkManager connection profile:

```
up ip addr add 192.168.1.200/24 dev enp3s0
```

Or create `/etc/networkd-dispatcher/routable.d/10-vsecc-alias`:

```bash
#!/bin/bash
ip addr add 192.168.1.200/24 dev enp3s0 2>/dev/null || true
```

```bash
sudo chmod +x /etc/networkd-dispatcher/routable.d/10-vsecc-alias
```

---

## 4. Software Prerequisites

### 4.1 Python packages

```bash
.venv/bin/pip install requests paho-mqtt
```

### 4.2 pdflatex (for PDF reports)

```bash
sudo apt install texlive-latex-base texlive-latex-extra texlive-fonts-recommended
```

Verify:
```bash
pdflatex --version
```

### 4.3 MQTT monitor (optional, for debugging)

```bash
sudo apt install mosquitto-clients

# Subscribe to all vSECC events (excluding CP-voltage noise):
mosquitto_sub -h 192.168.1.166 -p 1883 \
  -u vector -P vector \
  -t "vsecc/connector/#" \
  -t "vsecc/ocpp_connection_status"
```

---

## 5. Configuration

All constants are at the top of each test file. Defaults are:

| Constant | Default | Description |
|----------|---------|-------------|
| `VSECC_BASE` | `http://192.168.1.166/api` | vSECC REST endpoint |
| `MQTT_HOST` | `192.168.1.166` | vSECC MQTT broker |
| `CP_ID` | `rddQC4000001` | Charge point identity |
| `RFID_TAG` | `RFID_TEST` | RFID tag used for authorization |
| `EV_WAIT_SEC` | `60` | Seconds to wait for EV plug event (0 = skip) |
| `BOOT_WAIT_SEC` | `90` | Seconds to wait for vSECC reboot after Hard Reset |
| `LOCAL_STOP_WAIT_SEC` | `30` | Seconds to wait for RFID card tap to stop session |
| `EXPIRY_WAIT_SEC` | `90` | Seconds to wait for reservation to expire (section 5) |

### 5.1 Running WITHOUT a real EV

Set `EV_WAIT_SEC = 0` in the test file before running.  
EV-dependent items will record **WARN** instead of blocking for 60 s.

```bash
# Quick edit before running:
sed -i 's/^EV_WAIT_SEC = 60/EV_WAIT_SEC = 0/' tests/vsecc/test_mea_sectionN.py
```

### 5.2 Running WITH a real EV

Leave `EV_WAIT_SEC = 60` (or increase it).  
Connect the EV to the vSECC connector when the test prints a prompt like:

```
...waiting up to 60s for StatusNotification Preparing (EV plug)...
```

---

## 6. Running the Tests

All test scripts are standalone Python programs (not pytest).  
Run from the **project root**:

```bash
.venv/bin/python3 tests/vsecc/test_mea_sectionN.py
```

Results are saved to `tex/vsecc_sectionN_results.json` automatically.

### 6.1 Section-by-section guide

| Section | File | EV needed | Notes |
|---------|------|-----------|-------|
| **1** Charger Configuration | `test_mea_section1.py` | No | GetConfiguration, ChangeConfiguration, identity checks |
| **2** Auto Charge | `test_mea_section2.py` | **Yes** | All 21 items require EV plug for auto-charge flow |
| **3** Normal Operation | `test_mea_section3.py` | **Yes** | Local RFID + RemoteStart; 16 of 19 items need EV |
| **4** Reset Check | `test_mea_section4.py` | **Yes** | 15 of 21 items need EV (charging during reset) |
| **5** Reservation | `test_mea_section5.py` | **Yes** | Flow 3 (9 items) needs EV; Flows 1–2 work without |
| **6** Charging Profile | `test_mea_section6.py` | **Yes** | All charging-profile items need active EV session |
| **7** Abnormal Operation | `test_mea_section7.py` | **Yes** | E-stop, door, power loss all require EV plugged in |
| **8** Dual Connector | `test_mea_section8.py` | N/A | All SKIP — single connector device |
| **9** V2G / BPT | `test_mea_section9.py` | Partial | Config items (9.1–9.3) no EV; BPT items (9.4) need EV |
| **10** CSMS Commands | `test_mea_section10.py` | No | All 23 commands testable via REST without EV |
| **11** Performance | `test_mea_section11.py` | No | Reconnect time only; 3× Hard Reset |

> **Important:** Sections 2–7 have the majority of items dependent on a physical EV being connected. Without EV, these items record **WARN** and cannot demonstrate compliance. Connect a real EV before running sections 2–7 to obtain **PASS** results for the full test suite.

### 6.2 Run all sections in sequence

```bash
# Without EV — API connectivity check only (sections 2–7 will WARN):
for s in 1 2 3 4 5 6 7 8 9 10 11; do
  sed -i 's/^EV_WAIT_SEC = 60/EV_WAIT_SEC = 0/' tests/vsecc/test_mea_section${s}.py 2>/dev/null
  echo "=== Section $s ===" && .venv/bin/python3 tests/vsecc/test_mea_section${s}.py
done

# With EV — recommended for compliance (restore EV_WAIT_SEC first):
for s in 2 3 4 5 6 7; do
  sed -i 's/^EV_WAIT_SEC = 0/EV_WAIT_SEC = 60/' tests/vsecc/test_mea_section${s}.py 2>/dev/null
done
for s in 1 2 3 4 5 6 7 8 9 10 11; do
  echo "=== Section $s ===" && .venv/bin/python3 tests/vsecc/test_mea_section${s}.py
done
```

---

## 7. Running With a Real EV — Step-by-Step

### Pre-run checklist

- [ ] vSECC powered on and OCPP LED green (connected to MEA CSMS)
- [ ] Network alias active: `ip addr show enp3s0 | grep 192.168.1.200`
- [ ] EV charged enough to accept a session (not 100% full)
- [ ] RFID card `RFID_TEST` registered in MEA CSMS (or use an authorized card)
- [ ] `EV_WAIT_SEC = 60` set in the test file

### Section 3 — Normal Operation (most important EV section)

```
FLOW 1 (local RFID):
  3.1 Heartbeat observed
  3.2 BootNotification (vSECC connected)
  3.3 ──► PLUG IN EV when prompted
  3.4 Authorize RFID (tap card on vSECC)
  3.5 StartTransaction
  ...charging...
  3.8 ──► TAP RFID card again to stop session
      (or test falls back to RemoteStop after 30 s)
  3.12 ──► UNPLUG EV when prompted

FLOW 2 (RemoteStart):
  3.13 ──► PLUG IN EV when prompted
  ...test sends RemoteStart automatically...
  3.16 RemoteStop sent automatically
  3.19 ──► UNPLUG EV when prompted
```

### Section 4 — Reset (vSECC reboots)

After items 4.1 and 4.18 (Hard Reset), the vSECC physically reboots.  
**Wait ~90 s** for the OCPP LED to go green again before the test continues.  
The test waits automatically up to `BOOT_WAIT_SEC` seconds.

### Section 7 — Abnormal Operation (manual steps)

Some items require physical interaction:

| Item | Action required |
|------|----------------|
| 7.4 E-stop | Press the emergency stop button on the vSECC |
| 7.5 Door open | Open the vSECC enclosure door (if equipped) |
| 7.6 Power loss | The test simulates this with Hard Reset |
| 7.7 Local list | Automatic (SendLocalList via REST) |

If the hardware action cannot be performed, the item records **WARN**.

---

## 8. Building PDF Reports

After running a test (which saves the JSON result file):

```bash
# Single section:
.venv/bin/python3 tests/vsecc/generate_section5_report.py

# All sections at once:
for s in 1 2 3 4 5 6 7 8 9 10 11; do
  .venv/bin/python3 tests/vsecc/generate_section${s}_report.py
done
```

Output files:

```
tex/vsecc_section2_report.pdf
tex/vsecc_section3_report.pdf
...
tex/vsecc_section11_report.pdf
```

---

## 9. Interpreting Results

| Status | Meaning |
|--------|---------|
| **PASS** | Command accepted / event observed as expected |
| **FAIL** | Command rejected or endpoint returned HTTP error |
| **WARN** | Event not observed within timeout, or physical step needed |
| **SKIP** | Test not applicable (Section 8: single connector device) |

### Known MEA sandbox limitations (expected FAILs)

These REST API endpoints return HTTP 404 on the MEA sandbox — they are not available, regardless of vSECC support:

| OCPP Message | MEA REST endpoint | Status |
|---|---|---|
| TriggerMessage | `/remote/triggerMessage` | 404 |
| ChangeAvailability | `/remote/changeAvailability` | 404 |
| GetDiagnostics | `/remote/getDiagnostics` | 404 |
| UpdateFirmware | `/remote/updateFirmware` | 404 |
| SendLocalList | `/remote/sendLocalList` | 404 |
| GetLocalListVersion | `/cmd/chargepoint/getLocalListVersion` | 404 |

These FAILs reflect MEA sandbox API coverage, not vSECC compliance issues.

---

## 10. Troubleshooting

### vSECC not reachable (Connection timeout)

```bash
# Check alias:
ip addr show enp3s0 | grep 192.168.1

# Re-add if missing:
sudo ip addr add 192.168.1.200/24 dev enp3s0

# Verify:
ping 192.168.1.166
```

### vSECC not connected to MEA CSMS

Check OCPP LED on the vSECC board.  
Test will print: `ERROR: vSECC not connected to MEA CSMS` and fail all items.

```bash
# Monitor OCPP connection status:
mosquitto_sub -h 192.168.1.166 -u vector -P vector \
  -t "vsecc/ocpp_connection_status"
```

### MQTT events not received

The vSECC MQTT broker is internal (not exposed to the public internet).  
Ensure the PC is on the `192.168.1.x` subnet (alias active).

```bash
mosquitto_sub -h 192.168.1.166 -p 1883 -u vector -P vector \
  -t "vsecc/connector/1/status/#" -v
```

### pdflatex not found

```bash
sudo apt install texlive-latex-base texlive-latex-extra texlive-fonts-recommended
```

### Session stuck / test hangs

The test may be waiting for an MQTT event that will not arrive.  
Press `Ctrl+C` to abort. Partial results are **not** saved.  
Rerun after resolving the issue.

### Hard Reset — vSECC does not reconnect

After a Hard Reset, the vSECC typically reboots in 30–90 s.  
If it does not reconnect within `BOOT_WAIT_SEC`, check:
- vSECC power is stable
- OCPP URL is correctly configured on the vSECC
- MEA CSMS sandbox is reachable from the vSECC network

---

## 11. Quick Reference

```bash
# 1. Add network alias (required every session):
sudo ip addr add 192.168.1.200/24 dev enp3s0

# 2. Verify vSECC is reachable:
ping -c 2 192.168.1.166

# 3. Monitor MQTT (separate terminal):
mosquitto_sub -h 192.168.1.166 -u vector -P vector \
  -t "vsecc/connector/#" -t "vsecc/ocpp_connection_status" -v

# 4. Run a section test:
.venv/bin/python3 tests/vsecc/test_mea_section3.py

# 5. Build PDF report:
.venv/bin/python3 tests/vsecc/generate_section3_report.py

# 6. Run all sections + build all PDFs:
for s in 1 2 3 4 5 6 7 8 9 10 11; do
  .venv/bin/python3 tests/vsecc/test_mea_section${s}.py &&
  .venv/bin/python3 tests/vsecc/generate_section${s}_report.py
done
```

---

## 12. File Structure

```
MEA-V2G/
├── tests/vsecc/
│   ├── test_mea_section2.py     ← Section 2: Auto Charge
│   ├── test_mea_section3.py     ← Section 3: Normal Operation
│   ├── test_mea_section4.py     ← Section 4: Reset Check
│   ├── test_mea_section5.py     ← Section 5: Reservation
│   ├── test_mea_section6.py     ← Section 6: Charging Profile
│   ├── test_mea_section7.py     ← Section 7: Abnormal Operation
│   ├── test_mea_section8.py     ← Section 8: Dual Connector (all SKIP)
│   ├── test_mea_section9.py     ← Section 9: V2G / BPT
│   ├── test_mea_section10.py    ← Section 10: CSMS Commands
│   ├── test_mea_section11.py    ← Section 11: Performance
│   ├── generate_section2_report.py
│   ├── generate_section3_report.py
│   ├── ...
│   └── generate_section11_report.py
├── tex/
│   ├── vsecc_section2_results.json   ← raw results (auto-generated)
│   ├── vsecc_section2_report.pdf     ← PDF report (auto-generated)
│   └── ...
└── docs/
    └── vsecc_test_manual.md          ← this file
```

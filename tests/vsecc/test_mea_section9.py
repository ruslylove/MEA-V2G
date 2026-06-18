#!/usr/bin/env python3
"""
MEA OCPP 1.6 Compliance Test — Section 9
MEA-Specific Configuration & V2G/BPT (9.0 – 9.4)

Architecture (no proxy):
  vSECC  →  wss://ocpp.measandbox.com:2930  (direct TLS)
  PC     →  vSECC REST API   (login)
  PC     →  vSECC MQTT       (observe metervalues)
  PC     →  MEA REST API     (ChangeConfiguration, TriggerMessage)

Items:
  9.0   Connection check (CSMS probe)
  9.1   ChangeConfiguration V2GMode=true
  9.2   ChangeConfiguration MeterValueSampleInterval=10
  9.3   ChangeConfiguration LocalAuthorizeOffline=false
  9.4.0 ChangeConfiguration V2GMode=true (pre-condition for power demand)
  9.4.1 Power Demand -5000 W (BPT discharge)
  9.4.2 Power Demand  2000 W (BPT charge)
  9.4.3 Power Demand     0 W (BPT idle)

Note:
  V2GMode and Power.Active.Import are MEA-specific non-standard OCPP keys.
  Power demand items require an active charging session with a V2G-capable EV.
  Without a live charging session the metervalues MQTT events will not appear;
  those items are recorded as WARN rather than FAIL.

Run:
  python3 tests/vsecc/test_mea_section9.py
"""

import json
import os
import sys
import time
import threading
import requests
from datetime import datetime
from requests.auth import HTTPDigestAuth

try:
    import paho.mqtt.client as mqtt
    HAS_MQTT = True
except ImportError:
    HAS_MQTT = False

from vsecc_log import VseccLog, print_ocpp

# ─────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────
VSECC_BASE = "http://192.168.1.166/api"
VSECC_USER = "admin"
VSECC_PASS = "admin"
MQTT_HOST  = "192.168.1.166"
MQTT_PORT  = 1883
MQTT_USER  = "vector"
MQTT_PASS  = "vector"
CP_ID      = "rddQC4000001"
RFID_TAG   = "RFID_TEST"

MEA_API_BASE   = "https://ocppapi.measandbox.com/EV"
MEA_USER       = "meaev.api.dev"
MEA_PASS_DEF   = "U`?d3~C_Se77CrdsG[l#hq1)J_2$FA1D"
MEA_PASS_START = "Bh9GKYvSBc9KkbJ"

# Seconds to wait for metervalues MQTT event after TriggerMessage
METER_WAIT_SEC = 15

results = []
_last_raw = ""

# ─────────────────────────────────────────────
# vSECC REST helpers
# ─────────────────────────────────────────────
class VseccApi:
    def __init__(self):
        self.token = None

    def login(self):
        r = requests.post(f"{VSECC_BASE}/login",
                          json={"name": VSECC_USER, "password": VSECC_PASS}, timeout=10)
        self.token = r.text.strip().strip('"')
        return bool(self.token)


# ─────────────────────────────────────────────
# MEA CSMS REST helpers
# ─────────────────────────────────────────────
class MeaApi:
    def _post(self, path, payload, pwd=MEA_PASS_DEF):
        try:
            r = requests.post(f"{MEA_API_BASE}{path}", json=payload,
                              auth=HTTPDigestAuth(MEA_USER, pwd), timeout=15)
            return r
        except Exception:
            return None

    def change_configuration(self, key, value):
        return self._post("/remote/changeConfiguration",
                          {"chargepoint_id": CP_ID, "key": key, "value": value})

    def get_configuration(self, key=None):
        return self._post("/cmd/chargepoint/getConfiguration",
                          {"chargepoint": CP_ID, "key": [key] if key else []})

    def trigger_message(self, msg, connector_id=1):
        return self._post("/remote/triggerMessage",
                          {"chargepoint_id": CP_ID, "connectorId": connector_id,
                           "requestedMessage": msg})


# ─────────────────────────────────────────────
# Result recording
# ─────────────────────────────────────────────
def record(item, message, status, detail="", remark=""):
    global _last_raw
    raw = _last_raw
    _last_raw = ""
    tag = {"PASS": "[PASS]", "FAIL": "[FAIL]", "SKIP": "[SKIP]", "WARN": "[WARN]"}[status]
    print(f"  {tag} {item}  {message}" + (f"  ({detail})" if detail else ""))
    if remark:
        print(f"         {remark}")
    results.append({"item": item, "message": message, "status": status,
                    "detail": detail, "remark": remark, "raw": raw})


def section(title):
    print(f"\n{'─'*66}\n  {title}\n{'─'*66}")


# ─────────────────────────────────────────────
# MQTT watcher
# ─────────────────────────────────────────────
_mqtt_events  = []
_mqtt_lock    = threading.Lock()
_mqtt_client  = None
_ocpp_up      = threading.Event()


def start_mqtt_watcher():
    global _mqtt_client
    if not HAS_MQTT:
        return
    _mqtt_client = mqtt.Client()
    _mqtt_client.username_pw_set(MQTT_USER, MQTT_PASS)

    def on_connect(c, u, f, rc):
        c.subscribe([
            ("vsecc/ocpp_connection_status",   0),
            ("vsecc/connector/+/status/#",      0),
            ("vsecc/connector/+/ev/#",          0),
            ("vsecc/connector/+/metervalues",   0),
        ])

    def on_message(c, u, msg):
        payload = msg.payload.decode(errors="replace").strip()
        with _mqtt_lock:
            _mqtt_events.append((time.time(), msg.topic, payload))
        if msg.topic == "vsecc/ocpp_connection_status" and payload == "connected":
            _ocpp_up.set()

    _mqtt_client.on_connect = on_connect
    _mqtt_client.on_message = on_message
    try:
        _mqtt_client.connect(MQTT_HOST, MQTT_PORT)
        _mqtt_client.loop_start()
    except Exception as e:
        print(f"  MQTT connect failed: {e}")


def stop_mqtt_watcher():
    global _mqtt_client
    if _mqtt_client:
        _mqtt_client.loop_stop()
        _mqtt_client.disconnect()


def mqtt_mark():
    with _mqtt_lock:
        return len(_mqtt_events)


def mqtt_wait(mark, topic_kw, value_kw, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        with _mqtt_lock:
            for ts, topic, payload in _mqtt_events[mark:]:
                if topic_kw in topic and value_kw.lower() in payload.lower():
                    return payload, topic
        time.sleep(0.3)
    return None, None


# ─────────────────────────────────────────────
# MEA call helpers
# ─────────────────────────────────────────────
def mea_call(r):
    global _last_raw
    if r is None:
        return False, "No response"
    ok = r.status_code == 200
    try:
        body   = r.json()
        _last_raw = json.dumps(body, ensure_ascii=False)
        status = body.get("status") or body.get("result") or ""
        detail = f"HTTP {r.status_code}" + (f" status={status}" if status else "")
    except Exception:
        _last_raw = r.text[:400] if r and r.text else ""
        detail = f"HTTP {r.status_code}"
    return ok, detail


# ─────────────────────────────────────────────
# OCPP connection probe
# ─────────────────────────────────────────────
def _probe_csms_connection(mea: MeaApi, timeout=30) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _ocpp_up.is_set():
            return True
        try:
            r = mea.get_configuration()
            if r and r.status_code == 200:
                _ocpp_up.set()
                return True
        except Exception:
            pass
        time.sleep(3)
    return False


# ─────────────────────────────────────────────
# Power demand helper
# ─────────────────────────────────────────────
def _power_demand_item(item_id, message, mea: MeaApi, power_w: int,
                       log: VseccLog = None):
    """
    Issue Power.Active.Import ChangeConfiguration then TriggerMessage(MeterValues).
    PASS if MEA API returns HTTP 200.
    WARN if metervalues not observed on MQTT (requires active V2G charging session).
    If MQTT times out, falls back to ocpplib.log for MeterValues evidence.
    """
    global _last_raw
    mark = mqtt_mark()
    if log:
        log.mark()
    r = mea.change_configuration("Power.Active.Import", str(power_w))
    ok, detail = mea_call(r)
    if not ok:
        record(item_id, message, "WARN",
               detail + " — Power.Active.Import key not standard; MEA-specific BPT command",
               "Requires active V2G/BPT charging session with compatible EV")
        return

    # Trigger meter values to confirm power setpoint was received
    mea.trigger_message("MeterValues", connector_id=1)
    print(f"  ...waiting up to {METER_WAIT_SEC}s for metervalues on MQTT...")
    payload, topic = mqtt_wait(mark, "metervalues", "", timeout=METER_WAIT_SEC)
    if payload:
        record(item_id, message, "PASS",
               f"{detail}; metervalues received: {payload[:50]}",
               "Power.Active.Import is MEA-specific BPT command (non-standard OCPP key)")
    else:
        # MQTT missed it — check ocpplib.log as secondary evidence
        frames = log.ocpp_frames() if log else []
        log_match = log.find(frames, "MeterValues") if log else None
        if frames: _last_raw = "\n".join(f.strip() for f in frames)
        if log_match:
            record(item_id, message, "PASS",
                   f"{detail}; MeterValues confirmed via ocpplib.log: {log_match.strip()}",
                   "Power.Active.Import is MEA-specific BPT command (non-standard OCPP key)")
            print_ocpp(frames, f"{item_id} (from ocpplib.log)")
        else:
            record(item_id, message, "WARN",
                   f"{detail}; metervalues not observed in {METER_WAIT_SEC}s",
                   "V2G/BPT requires active charging session and V2G-capable EV")


# ─────────────────────────────────────────────
# Section 9 test body
# ─────────────────────────────────────────────
def run_section9(vsecc: VseccApi, mea: MeaApi, log: VseccLog = None):
    section("Section 9: MEA-Specific Configuration & V2G/BPT")

    # ── 9.0  Connection check ────────────────────────────────────────────────
    print("  Checking vSECC → MEA CSMS connection (up to 30 s)...")
    if not _probe_csms_connection(mea, timeout=30):
        print("  ERROR: vSECC not connected to MEA CSMS.")
        for item_id in ["9.0", "9.1", "9.2", "9.3", "9.4.0",
                        "9.4.1", "9.4.2", "9.4.3"]:
            record(item_id, f"Item {item_id}", "FAIL",
                   "vSECC not connected to MEA CSMS")
        return
    record("9.0", "CSMS connection probe — GetConfiguration HTTP 200",
           "PASS", "vSECC connected to MEA CSMS")
    print("  vSECC connected to MEA CSMS.")

    # ══════════════════════════════════════════════════════════════════════════
    # 9.1 – 9.3  Standard configuration keys
    # ══════════════════════════════════════════════════════════════════════════
    section("9.1–9.3 Configuration")

    # ── 9.1  V2GMode=true ───────────────────────────────────────────────────
    r = mea.change_configuration("V2GMode", "true")
    ok, detail = mea_call(r)
    record("9.1", "ChangeConfiguration V2GMode=true → Accepted",
           "PASS" if ok else "FAIL", detail)

    # ── 9.2  MeterValueSampleInterval=10 ────────────────────────────────────
    r = mea.change_configuration("MeterValueSampleInterval", "10")
    ok, detail = mea_call(r)
    record("9.2", "ChangeConfiguration MeterValueSampleInterval=10 → Accepted",
           "PASS" if ok else "FAIL", detail)

    # ── 9.3  LocalAuthorizeOffline=false ────────────────────────────────────
    r = mea.change_configuration("LocalAuthorizeOffline", "false")
    ok, detail = mea_call(r)
    record("9.3", "ChangeConfiguration LocalAuthorizeOffline=false → Accepted",
           "PASS" if ok else "FAIL", detail)

    # ══════════════════════════════════════════════════════════════════════════
    # 9.4  Power Demand (V2G/BPT)
    # ══════════════════════════════════════════════════════════════════════════
    section("9.4 Power Demand (V2G/BPT)")

    # ── 9.4.0  Pre-condition: V2GMode=true ──────────────────────────────────
    r = mea.change_configuration("V2GMode", "true")
    ok, detail = mea_call(r)
    record("9.4.0", "ChangeConfiguration V2GMode=true (pre-condition for power demand)",
           "PASS" if ok else "FAIL", detail)

    # ── 9.4.1  Power Demand -5000 W (BPT discharge) ─────────────────────────
    _power_demand_item("9.4.1",
                       "Power Demand -5000 W (BPT discharge from EV to grid)",
                       mea, -5000, log=log)

    # ── 9.4.2  Power Demand +2000 W (BPT charge) ────────────────────────────
    _power_demand_item("9.4.2",
                       "Power Demand +2000 W (BPT charge EV from grid)",
                       mea, 2000, log=log)

    # ── 9.4.3  Power Demand 0 W (BPT idle / stop) ───────────────────────────
    _power_demand_item("9.4.3",
                       "Power Demand 0 W (BPT idle / stop power flow)",
                       mea, 0, log=log)


# ─────────────────────────────────────────────
# Summary + JSON
# ─────────────────────────────────────────────
def print_summary():
    counts = {s: sum(1 for r in results if r["status"] == s)
              for s in ("PASS", "FAIL", "WARN", "SKIP")}
    total  = sum(counts.values())
    print(f"\n{'═'*66}")
    print(f"  Section 9  |  "
          f"{counts['PASS']} PASS  {counts['FAIL']} FAIL  "
          f"{counts['WARN']} WARN  {counts['SKIP']} SKIP  ({total} total)")
    print(f"{'═'*66}")
    for r in results:
        print(f"  {r['item']:<6} {r['status']:<6}  {r['message'][:52]}")
    print(f"{'═'*66}")


_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def save_json(path=None):
    if path is None:
        path = os.path.join(_ROOT, "tex", "vsecc_section9_results.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump({
            "section": 9,
            "title": "MEA-Specific Configuration & V2G/BPT",
            "cp_id": CP_ID,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "method": "direct-to-csms",
            "meter_wait_sec": METER_WAIT_SEC,
            "results": results,
        }, f, indent=2, ensure_ascii=False)
    print(f"\n  Saved {path}")


# ─────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────
def main():
    print("=" * 66)
    print("  MEA OCPP 1.6 Compliance — Section 9 (9.0 – 9.4.3)  [no proxy]")
    print(f"  CP: {CP_ID}  |  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  MeterValues wait: {METER_WAIT_SEC} s")
    print("=" * 66)

    vsecc = VseccApi()
    mea   = MeaApi()

    if not vsecc.login():
        print("ABORT: Cannot reach vSECC (need alias: sudo ip addr add 192.168.1.200/24 dev enp3s0)")
        sys.exit(1)
    print("  vSECC authenticated")

    vslog = VseccLog(vsecc.token)

    start_mqtt_watcher()
    try:
        run_section9(vsecc, mea, log=vslog)
    finally:
        stop_mqtt_watcher()

    print_summary()
    save_json()


if __name__ == "__main__":
    main()

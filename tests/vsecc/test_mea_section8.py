#!/usr/bin/env python3
"""
MEA OCPP 1.6 Compliance Test — Section 8
Dual Connector Verification
Items 8.1.1–8.1.18, 8.2.1–8.2.16, 8.3.1–8.3.16 per OCPP1.6_FORM_MEA.pdf Annex B.

ALL ITEMS ARE SKIP — the vSECC.single board has only ONE connector.
Section 8 tests concurrent dual-connector scenarios:
  8.1  Concurrent RemoteStart (dual connectors)
  8.2  Shared Emergency Stop (dual connectors)
  8.3  Power Loss (dual connectors)

None of these can be executed on a single-connector device.
The test still performs the standard login / MQTT setup for consistency,
then immediately records every item as SKIP.

Run:
  python3 tests/vsecc/test_mea_section8.py
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

SKIP_DETAIL = "vSECC.single has one connector — dual-connector scenarios N/A"

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

    def get_configuration(self, key=None):
        return self._post("/cmd/chargepoint/getConfiguration",
                          {"chargepoint": CP_ID, "key": [key] if key else []})


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
# Section 8 test body
# ─────────────────────────────────────────────

# Item definitions for all three sub-sections
_ITEMS_81 = [
    ("8.1.1",  "Concurrent RemoteStart — Connector 1 Accepted"),
    ("8.1.2",  "Concurrent RemoteStart — Connector 2 Accepted"),
    ("8.1.3",  "StartTransaction — Connector 1"),
    ("8.1.4",  "StartTransaction — Connector 2"),
    ("8.1.5",  "StatusNotification Charging — Connector 1"),
    ("8.1.6",  "StatusNotification Charging — Connector 2"),
    ("8.1.7",  "MeterValues — Connector 1"),
    ("8.1.8",  "MeterValues — Connector 2"),
    ("8.1.9",  "RemoteStop — Connector 1"),
    ("8.1.10", "RemoteStop — Connector 2"),
    ("8.1.11", "StopTransaction — Connector 1"),
    ("8.1.12", "StopTransaction — Connector 2"),
    ("8.1.13", "StatusNotification Finishing — Connector 1"),
    ("8.1.14", "StatusNotification Finishing — Connector 2"),
    ("8.1.15", "StatusNotification Available — Connector 1"),
    ("8.1.16", "StatusNotification Available — Connector 2"),
    ("8.1.17", "Transaction isolation — Connector 1 not affected by Connector 2 stop"),
    ("8.1.18", "Transaction isolation — Connector 2 not affected by Connector 1 stop"),
]

_ITEMS_82 = [
    ("8.2.1",  "Emergency Stop signal sent"),
    ("8.2.2",  "RemoteStop broadcast — Connector 1"),
    ("8.2.3",  "RemoteStop broadcast — Connector 2"),
    ("8.2.4",  "StopTransaction (EmergencyStop reason) — Connector 1"),
    ("8.2.5",  "StopTransaction (EmergencyStop reason) — Connector 2"),
    ("8.2.6",  "StatusNotification Faulted — Connector 1"),
    ("8.2.7",  "StatusNotification Faulted — Connector 2"),
    ("8.2.8",  "StatusNotification Unavailable (after fault clear) — Connector 1"),
    ("8.2.9",  "StatusNotification Unavailable (after fault clear) — Connector 2"),
    ("8.2.10", "ChangeAvailability Operative — Connector 1"),
    ("8.2.11", "ChangeAvailability Operative — Connector 2"),
    ("8.2.12", "StatusNotification Available — Connector 1"),
    ("8.2.13", "StatusNotification Available — Connector 2"),
    ("8.2.14", "New session possible after emergency stop — Connector 1"),
    ("8.2.15", "New session possible after emergency stop — Connector 2"),
    ("8.2.16", "Shared emergency stop clears both connectors simultaneously"),
]

_ITEMS_83 = [
    ("8.3.1",  "Power loss event detected"),
    ("8.3.2",  "Active session aborted — Connector 1"),
    ("8.3.3",  "Active session aborted — Connector 2"),
    ("8.3.4",  "StopTransaction (PowerLoss reason) — Connector 1"),
    ("8.3.5",  "StopTransaction (PowerLoss reason) — Connector 2"),
    ("8.3.6",  "vSECC power loss BootNotification after restore"),
    ("8.3.7",  "StatusNotification Available after power restore — Connector 1"),
    ("8.3.8",  "StatusNotification Available after power restore — Connector 2"),
    ("8.3.9",  "Session data integrity after power restore — Connector 1"),
    ("8.3.10", "Session data integrity after power restore — Connector 2"),
    ("8.3.11", "RemoteStart accepted after power restore — Connector 1"),
    ("8.3.12", "RemoteStart accepted after power restore — Connector 2"),
    ("8.3.13", "StartTransaction after power restore — Connector 1"),
    ("8.3.14", "StartTransaction after power restore — Connector 2"),
    ("8.3.15", "StatusNotification Charging after power restore — Connector 1"),
    ("8.3.16", "StatusNotification Charging after power restore — Connector 2"),
]


def run_section8(vsecc: VseccApi, mea: MeaApi):
    section("Section 8: Dual Connector Verification")

    print("  NOTE: vSECC.single board — only one connector available.")
    print("        All Section 8 items require two connectors and are NOT APPLICABLE.")
    print()

    section("8.1 Concurrent RemoteStart (Dual Connectors)")
    for item_id, msg in _ITEMS_81:
        record(item_id, msg, "SKIP", SKIP_DETAIL)

    section("8.2 Shared Emergency Stop (Dual Connectors)")
    for item_id, msg in _ITEMS_82:
        record(item_id, msg, "SKIP", SKIP_DETAIL)

    section("8.3 Power Loss (Dual Connectors)")
    for item_id, msg in _ITEMS_83:
        record(item_id, msg, "SKIP", SKIP_DETAIL)


# ─────────────────────────────────────────────
# Summary + JSON
# ─────────────────────────────────────────────
def print_summary():
    counts = {s: sum(1 for r in results if r["status"] == s)
              for s in ("PASS", "FAIL", "WARN", "SKIP")}
    total  = sum(counts.values())
    print(f"\n{'═'*66}")
    print(f"  Section 8  |  "
          f"{counts['PASS']} PASS  {counts['FAIL']} FAIL  "
          f"{counts['WARN']} WARN  {counts['SKIP']} SKIP  ({total} total)")
    print(f"{'═'*66}")
    for r in results:
        print(f"  {r['item']:<8} {r['status']:<6}  {r['message'][:50]}")
    print(f"{'═'*66}")


_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def save_json(path=None):
    if path is None:
        path = os.path.join(_ROOT, "tex", "vsecc_section8_results.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump({
            "section": 8,
            "title": "Dual Connector Verification",
            "cp_id": CP_ID,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "method": "direct-to-csms",
            "note": "All items SKIP — vSECC.single has one connector; dual-connector scenarios N/A",
            "results": results,
        }, f, indent=2, ensure_ascii=False)
    print(f"\n  Saved {path}")


# ─────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────
def main():
    print("=" * 66)
    print("  MEA OCPP 1.6 Compliance — Section 8 (8.1.1 – 8.3.16)  [no proxy]")
    print(f"  CP: {CP_ID}  |  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("  ALL ITEMS: SKIP (vSECC.single — single connector device)")
    print("=" * 66)

    vsecc = VseccApi()
    mea   = MeaApi()

    if not vsecc.login():
        print("ABORT: Cannot reach vSECC (need alias: sudo ip addr add 192.168.1.200/24 dev enp3s0)")
        sys.exit(1)
    print("  vSECC authenticated")

    start_mqtt_watcher()
    try:
        run_section8(vsecc, mea)
    finally:
        stop_mqtt_watcher()

    print_summary()
    save_json()


if __name__ == "__main__":
    main()

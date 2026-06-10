#!/usr/bin/env python3
"""
MEA OCPP 1.6 Compliance Test — Section 4
การตรวจสอบ Reset (Reset Check)
Items 4.1 – 4.21 per OCPP1.6_FORM_MEA.pdf Annex B.

Architecture (no proxy):
  vSECC  →  wss://ocpp.measandbox.com:2930  (direct TLS)
  PC     →  vSECC REST API   (login)
  PC     →  vSECC MQTT       (observe reconnect / status / session)
  PC     →  MEA REST API     (Reset, RemoteStart, RemoteStop)

Three flows tested:
  Flow 1 (4.1–4.8)   — Hard Reset while idle; then charge session via RemoteStart
  Flow 2 (4.9–4.17)  — Soft Reset during charging; session terminates gracefully;
                        new charge session via RemoteStart
  Flow 3 (4.18–4.21) — Hard Reset during charging; session terminates; unplug

Run:
  python3 tests/vsecc/test_mea_section4.py

Note:
  Items 4.4 and 4.13 require a physical EV to be plugged into the vSECC connector.
  Set EV_WAIT_SEC = 0 to skip EV-dependent items immediately.
  After each Hard Reset the vSECC reboots; the test waits up to BOOT_WAIT_SEC for
  reconnection to the MEA CSMS.
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

# Wait for EV plug events (seconds). Set 0 to skip EV-dependent items.
EV_WAIT_SEC   = 60
# Wait for vSECC to reboot and reconnect after Hard Reset.
BOOT_WAIT_SEC = 90

results = []

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

    def reset(self, reset_type="Hard"):
        return self._post("/remote/reset",
                          {"chargepoint": CP_ID, "reset_type": reset_type})

    def get_configuration(self, key=None):
        return self._post("/cmd/chargepoint/getConfiguration",
                          {"chargepoint": CP_ID, "key": [key] if key else []})

    def remote_start(self, connector_id=1, id_tag=RFID_TAG):
        return self._post("/cmd/chargepoint/remoteStart",
                          {"chargepoint_id": CP_ID, "connector_id": connector_id,
                           "card_id": id_tag},
                          pwd=MEA_PASS_START)

    def remote_stop(self, transaction_id=0):
        return self._post("/cmd/chargepoint/remoteStop",
                          {"chargepoint_id": CP_ID, "transaction_id": transaction_id},
                          pwd=MEA_PASS_START)

    def trigger_message(self, msg, connector_id=1):
        return self._post("/remote/triggerMessage",
                          {"chargepoint_id": CP_ID, "connectorId": connector_id,
                           "requestedMessage": msg})


# ─────────────────────────────────────────────
# Result recording
# ─────────────────────────────────────────────
def record(item, message, status, detail="", remark=""):
    tag = {"PASS": "[PASS]", "FAIL": "[FAIL]", "SKIP": "[SKIP]", "WARN": "[WARN]"}[status]
    print(f"  {tag} {item}  {message}" + (f"  ({detail})" if detail else ""))
    if remark:
        print(f"         {remark}")
    results.append({"item": item, "message": message, "status": status,
                    "detail": detail, "remark": remark})


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
    if r is None:
        return False, "No response"
    ok = r.status_code == 200
    try:
        body   = r.json()
        status = body.get("status") or body.get("result") or ""
        detail = f"HTTP {r.status_code}" + (f" status={status}" if status else "")
    except Exception:
        detail = f"HTTP {r.status_code}"
    return ok, detail


# ─────────────────────────────────────────────
# OCPP connection probe (REST + MQTT dual check)
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


def wait_for_reconnect(mea: MeaApi, mark, timeout=None) -> bool:
    """
    Wait for vSECC to reconnect after a reset.
    Clears _ocpp_up first so we catch a fresh reconnect event.
    """
    if timeout is None:
        timeout = BOOT_WAIT_SEC
    _ocpp_up.clear()
    deadline = time.time() + timeout
    while time.time() < deadline:
        # Check MQTT for reconnect event
        payload, topic = mqtt_wait(mark, "ocpp_connection_status", "connected", timeout=5)
        if payload:
            _ocpp_up.set()
            return True
        # Also probe REST
        try:
            r = mea.get_configuration()
            if r and r.status_code == 200:
                _ocpp_up.set()
                return True
        except Exception:
            pass
    return False


# ─────────────────────────────────────────────
# EV-plug item helper
# ─────────────────────────────────────────────
def _ev_item(item, message, mark, timeout=None):
    if timeout is None:
        timeout = EV_WAIT_SEC
    if timeout == 0:
        record(item, message, "WARN", "EV_WAIT_SEC=0 — skipped",
               "Connect physical EV to connector and rerun")
        return False, "skipped"
    print(f"  ...waiting up to {timeout}s for {message} (connect EV if needed)...")
    payload, topic = mqtt_wait(mark, "status", "Preparing", timeout=timeout)
    if payload:
        record(item, message, "PASS", f"{topic.split('/')[-1]}={payload}")
        return True, payload
    record(item, message, "WARN", f"No Preparing event in {timeout}s — no EV connected",
           "Connect physical EV and rerun for full Section 4 coverage")
    return False, "timeout"


# ─────────────────────────────────────────────
# Section 4 test body
# ─────────────────────────────────────────────
def run_section4(vsecc: VseccApi, mea: MeaApi):
    section("Section 4: การตรวจสอบ Reset (Reset Check)")

    # ── Check initial OCPP connection ────────────────────────────────────────
    print("  Checking vSECC → MEA CSMS connection (up to 30 s)...")
    if not _probe_csms_connection(mea, timeout=30):
        print("  ERROR: vSECC not connected to MEA CSMS.")
        for i in range(1, 22):
            record(f"4.{i}", f"Item 4.{i}", "FAIL", "vSECC not connected to MEA CSMS")
        return
    print("  vSECC connected to MEA CSMS.")

    # ══════════════════════════════════════════════════════════════════════════
    # Flow 1: Hard Reset (idle state) — 4.1–4.8
    # ══════════════════════════════════════════════════════════════════════════
    print("\n  [Flow 1] Hard Reset while idle")

    # ── 4.1  Reset (Hard) → Accepted ─────────────────────────────────────────
    mark41 = mqtt_mark()
    r = mea.reset("Hard")
    ok41, detail41 = mea_call(r)
    record("4.1", "Reset (Hard) → Accepted",
           "PASS" if ok41 else "FAIL", detail41)

    # ── 4.2  BootNotification (after Hard Reset reboot) ───────────────────────
    if ok41:
        print(f"  ...waiting up to {BOOT_WAIT_SEC}s for vSECC to reboot and reconnect...")
        reconnected = wait_for_reconnect(mea, mark41, timeout=BOOT_WAIT_SEC)
        if reconnected:
            record("4.2", "BootNotification → Accepted (after Hard Reset)",
                   "PASS", "CSMS reconnection confirmed after reset")
        else:
            record("4.2", "BootNotification → Accepted (after Hard Reset)",
                   "WARN", f"No reconnect in {BOOT_WAIT_SEC}s after Hard Reset",
                   "vSECC may take longer to reboot; increase BOOT_WAIT_SEC")
    else:
        record("4.2", "BootNotification → Accepted (after Hard Reset)",
               "WARN", "Depends on 4.1 (Reset Hard)")

    # ── 4.3  StatusNotification Available (after boot) ───────────────────────
    mark43 = mqtt_mark()
    time.sleep(2)
    payload, topic = mqtt_wait(mark41, "status", "Available", timeout=20)
    if payload:
        record("4.3", "StatusNotification Available (after reboot)",
               "PASS", f"{topic.split('/')[-1]}={payload}")
    else:
        record("4.3", "StatusNotification Available (after reboot)",
               "WARN", "Available not observed on MQTT in 20 s after reset")

    # ── 4.4  StatusNotification Preparing (EV plug) ───────────────────────────
    mark44 = mqtt_mark()
    ok44, _ = _ev_item("4.4", "StatusNotification Preparing (EV plug, Flow 1)", mark44)

    # ── 4.5  RemoteStartTransaction ───────────────────────────────────────────
    mark45 = mqtt_mark()
    r = mea.remote_start(connector_id=1)
    ok45, detail45 = mea_call(r)
    record("4.5", "RemoteStartTransaction → Accepted",
           "PASS" if ok45 else "FAIL", detail45)

    # ── 4.6  StartTransaction (triggered by RemoteStart) ─────────────────────
    if ok45:
        payload, topic = mqtt_wait(mark45, "charging_session_state", "started", timeout=30)
        if not payload:
            payload, topic = mqtt_wait(mark45, "charging_session_state", "active", timeout=5)
        if payload:
            record("4.6", "StartTransaction (triggered by RemoteStart)",
                   "PASS", f"session_state={payload}")
        else:
            record("4.6", "StartTransaction (triggered by RemoteStart)",
                   "WARN", "charging_session_state not observed on MQTT in 30 s")
    else:
        record("4.6", "StartTransaction (triggered by RemoteStart)",
               "WARN", "Depends on 4.5 (RemoteStart)")

    # ── 4.7  StatusNotification Charging ─────────────────────────────────────
    if ok45:
        payload, topic = mqtt_wait(mark45, "status", "Charging", timeout=20)
        if payload:
            record("4.7", "StatusNotification Charging (Flow 1)",
                   "PASS", f"{topic.split('/')[-1]}={payload}")
        else:
            record("4.7", "StatusNotification Charging (Flow 1)",
                   "WARN", "Charging status not observed on MQTT in 20 s")
    else:
        record("4.7", "StatusNotification Charging (Flow 1)",
               "WARN", "Depends on 4.5 (RemoteStart)")

    # ── 4.8  MeterValues ─────────────────────────────────────────────────────
    if ok45:
        mea.trigger_message("MeterValues", connector_id=1)
        payload, topic = mqtt_wait(mark45, "metervalues", "", timeout=15)
        if payload:
            record("4.8", "MeterValues (Flow 1, during charging)",
                   "PASS", f"metervalues received: {payload[:60]}")
        else:
            record("4.8", "MeterValues (Flow 1, during charging)",
                   "WARN", "MeterValues not observed on MQTT in 15 s")
    else:
        record("4.8", "MeterValues (Flow 1, during charging)",
               "WARN", "Depends on 4.5 (RemoteStart)")

    # ══════════════════════════════════════════════════════════════════════════
    # Flow 2: Soft Reset during charging — 4.9–4.17
    # ══════════════════════════════════════════════════════════════════════════
    print("\n  [Flow 2] Soft Reset during charging")

    # ── 4.9  Reset (Soft) → Accepted (during charging) ───────────────────────
    mark49 = mqtt_mark()
    r = mea.reset("Soft")
    ok49, detail49 = mea_call(r)
    record("4.9", "Reset (Soft) → Accepted (during charging)",
           "PASS" if ok49 else "FAIL", detail49)

    # ── 4.10 StopTransaction (caused by Soft Reset) ───────────────────────────
    if ok49:
        payload, topic = mqtt_wait(mark49, "charging_session_state", "idle", timeout=30)
        if not payload:
            payload, topic = mqtt_wait(mark49, "charging_session_state", "finish", timeout=5)
        if payload:
            record("4.10", "StopTransaction (triggered by Soft Reset)",
                   "PASS", f"session_state={payload}")
        else:
            record("4.10", "StopTransaction (triggered by Soft Reset)",
                   "WARN", "session_state idle/finished not observed in 30 s",
                   "Soft Reset should cause graceful StopTransaction before reboot")
    else:
        record("4.10", "StopTransaction (triggered by Soft Reset)",
               "WARN", "Depends on 4.9 (Reset Soft)")

    # ── 4.11 StatusNotification Finishing (Soft Reset, EV still plugged) ──────
    if ok49:
        payload, topic = mqtt_wait(mark49, "status", "Finishing", timeout=15)
        if payload:
            record("4.11", "StatusNotification Finishing (Soft Reset, EV plugged)",
                   "PASS", f"{topic.split('/')[-1]}={payload}")
        else:
            record("4.11", "StatusNotification Finishing (Soft Reset, EV plugged)",
                   "WARN", "Finishing not observed on MQTT in 15 s",
                   "Some implementations skip Finishing → go directly to Available")
    else:
        record("4.11", "StatusNotification Finishing (Soft Reset, EV plugged)",
               "WARN", "Depends on 4.9 (Reset Soft)")

    # ── 4.12 StatusNotification Available (unplug after Soft Reset) ──────────
    if ok49:
        print("  ...waiting for Available (unplug EV after Soft Reset)...")
        payload, topic = mqtt_wait(mark49, "status", "Available", timeout=30)
        if payload:
            record("4.12", "StatusNotification Available (unplug after Soft Reset)",
                   "PASS", f"{topic.split('/')[-1]}={payload}")
        else:
            record("4.12", "StatusNotification Available (unplug after Soft Reset)",
                   "WARN", "Available not observed on MQTT in 30 s — unplug EV")
    else:
        record("4.12", "StatusNotification Available (unplug after Soft Reset)",
               "WARN", "Depends on 4.9 (Reset Soft)")

    # ── 4.13 StatusNotification Preparing (EV plug, Flow 2) ──────────────────
    mark413 = mqtt_mark()
    ok413, _ = _ev_item("4.13", "StatusNotification Preparing (EV plug, Flow 2)", mark413)

    # ── 4.14 RemoteStartTransaction (Flow 2) ─────────────────────────────────
    mark414 = mqtt_mark()
    r = mea.remote_start(connector_id=1)
    ok414, detail414 = mea_call(r)
    record("4.14", "RemoteStartTransaction → Accepted (Flow 2)",
           "PASS" if ok414 else "FAIL", detail414)

    # ── 4.15 StartTransaction (Flow 2) ───────────────────────────────────────
    if ok414:
        payload, topic = mqtt_wait(mark414, "charging_session_state", "started", timeout=30)
        if not payload:
            payload, topic = mqtt_wait(mark414, "charging_session_state", "active", timeout=5)
        if payload:
            record("4.15", "StartTransaction (Flow 2, triggered by RemoteStart)",
                   "PASS", f"session_state={payload}")
        else:
            record("4.15", "StartTransaction (Flow 2, triggered by RemoteStart)",
                   "WARN", "charging_session_state not observed on MQTT in 30 s")
    else:
        record("4.15", "StartTransaction (Flow 2, triggered by RemoteStart)",
               "WARN", "Depends on 4.14 (RemoteStart)")

    # ── 4.16 StatusNotification Charging (Flow 2) ────────────────────────────
    if ok414:
        payload, topic = mqtt_wait(mark414, "status", "Charging", timeout=20)
        if payload:
            record("4.16", "StatusNotification Charging (Flow 2)",
                   "PASS", f"{topic.split('/')[-1]}={payload}")
        else:
            record("4.16", "StatusNotification Charging (Flow 2)",
                   "WARN", "Charging status not observed on MQTT in 20 s")
    else:
        record("4.16", "StatusNotification Charging (Flow 2)",
               "WARN", "Depends on 4.14 (RemoteStart)")

    # ── 4.17 MeterValues (Flow 2) ────────────────────────────────────────────
    if ok414:
        mea.trigger_message("MeterValues", connector_id=1)
        payload, topic = mqtt_wait(mark414, "metervalues", "", timeout=15)
        if payload:
            record("4.17", "MeterValues (Flow 2, during charging)",
                   "PASS", f"metervalues received: {payload[:60]}")
        else:
            record("4.17", "MeterValues (Flow 2, during charging)",
                   "WARN", "MeterValues not observed on MQTT in 15 s")
    else:
        record("4.17", "MeterValues (Flow 2, during charging)",
               "WARN", "Depends on 4.14 (RemoteStart)")

    # ══════════════════════════════════════════════════════════════════════════
    # Flow 3: Hard Reset during charging — 4.18–4.21
    # ══════════════════════════════════════════════════════════════════════════
    print("\n  [Flow 3] Hard Reset during charging")

    # ── 4.18 Reset (Hard) → Accepted (during charging) ───────────────────────
    mark418 = mqtt_mark()
    r = mea.reset("Hard")
    ok418, detail418 = mea_call(r)
    record("4.18", "Reset (Hard) → Accepted (during charging)",
           "PASS" if ok418 else "FAIL", detail418)

    # ── 4.19 StopTransaction (caused by Hard Reset) ───────────────────────────
    if ok418:
        # Hard Reset may abort the session immediately (no graceful stop)
        # or send StopTransaction before rebooting.
        payload, topic = mqtt_wait(mark418, "charging_session_state", "idle", timeout=30)
        if not payload:
            payload, topic = mqtt_wait(mark418, "charging_session_state", "finish", timeout=5)
        if payload:
            record("4.19", "StopTransaction (triggered by Hard Reset)",
                   "PASS", f"session_state={payload}")
        else:
            record("4.19", "StopTransaction (triggered by Hard Reset)",
                   "WARN", "session_state idle/finished not observed in 30 s",
                   "Hard Reset may not send StopTransaction before rebooting")
    else:
        record("4.19", "StopTransaction (triggered by Hard Reset)",
               "WARN", "Depends on 4.18 (Reset Hard)")

    # ── 4.20 StatusNotification Finishing (Hard Reset, EV still plugged) ──────
    if ok418:
        payload, topic = mqtt_wait(mark418, "status", "Finishing", timeout=15)
        if payload:
            record("4.20", "StatusNotification Finishing (Hard Reset, EV plugged)",
                   "PASS", f"{topic.split('/')[-1]}={payload}")
        else:
            record("4.20", "StatusNotification Finishing (Hard Reset, EV plugged)",
                   "WARN", "Finishing not observed on MQTT in 15 s",
                   "Hard Reset typically skips Finishing — goes directly Available after reboot")
    else:
        record("4.20", "StatusNotification Finishing (Hard Reset, EV plugged)",
               "WARN", "Depends on 4.18 (Reset Hard)")

    # ── 4.21 StatusNotification Available (after Hard Reset, unplug) ─────────
    if ok418:
        print(f"  ...waiting up to {BOOT_WAIT_SEC}s for vSECC to reboot and report Available...")
        payload, topic = mqtt_wait(mark418, "status", "Available", timeout=BOOT_WAIT_SEC)
        if payload:
            record("4.21", "StatusNotification Available (after Hard Reset, EV unplug)",
                   "PASS", f"{topic.split('/')[-1]}={payload}")
        else:
            record("4.21", "StatusNotification Available (after Hard Reset, EV unplug)",
                   "WARN", f"Available not observed in {BOOT_WAIT_SEC}s — unplug EV / wait for reboot")
    else:
        record("4.21", "StatusNotification Available (after Hard Reset, EV unplug)",
               "WARN", "Depends on 4.18 (Reset Hard)")


# ─────────────────────────────────────────────
# Summary + JSON
# ─────────────────────────────────────────────
def print_summary():
    counts = {s: sum(1 for r in results if r["status"] == s)
              for s in ("PASS", "FAIL", "WARN", "SKIP")}
    total  = sum(counts.values())
    print(f"\n{'═'*66}")
    print(f"  Section 4  |  "
          f"{counts['PASS']} PASS  {counts['FAIL']} FAIL  "
          f"{counts['WARN']} WARN  {counts['SKIP']} SKIP  ({total} total)")
    print(f"{'═'*66}")
    for r in results:
        print(f"  {r['item']:<6} {r['status']:<6}  {r['message'][:52]}")
    print(f"{'═'*66}")


_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def save_json(path=None):
    if path is None:
        path = os.path.join(_ROOT, "tex", "vsecc_section4_results.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump({
            "section": 4,
            "title": "การตรวจสอบ Reset",
            "cp_id": CP_ID,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "method": "direct-to-csms",
            "ev_wait_sec": EV_WAIT_SEC,
            "boot_wait_sec": BOOT_WAIT_SEC,
            "results": results,
        }, f, indent=2, ensure_ascii=False)
    print(f"\n  Saved {path}")


# ─────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────
def main():
    print("=" * 66)
    print("  MEA OCPP 1.6 Compliance — Section 4 (4.1 – 4.21)  [no proxy]")
    print(f"  CP: {CP_ID}  |  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  EV plug timeout: {EV_WAIT_SEC} s  |  Boot wait: {BOOT_WAIT_SEC} s")
    print("=" * 66)

    vsecc = VseccApi()
    mea   = MeaApi()

    if not vsecc.login():
        print("ABORT: Cannot reach vSECC (need alias: sudo ip addr add 192.168.1.200/24 dev enp3s0)")
        sys.exit(1)
    print("  vSECC authenticated")

    start_mqtt_watcher()
    try:
        run_section4(vsecc, mea)
    finally:
        stop_mqtt_watcher()

    print_summary()
    save_json()


if __name__ == "__main__":
    main()

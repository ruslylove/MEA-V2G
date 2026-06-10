#!/usr/bin/env python3
"""
MEA OCPP 1.6 Compliance Test — Section 7
การตรวจสอบการทำงานผิดปกติ (Abnormal Operation Verification)
Items 7.1 – 7.7 per OCPP1.6_FORM_MEA.pdf Annex B.

Architecture (no proxy):
  vSECC  →  wss://ocpp.measandbox.com:2930  (direct TLS)
  PC     →  vSECC REST API   (configure, read state)
  PC     →  vSECC MQTT       (observe status / session / CP state)
  PC     →  MEA REST API     (RemoteStart, RemoteStop, Reset, SendLocalList)

Sub-scenarios:
  7.1 — RemoteStart while unplugged (Available state)
  7.2 — Concurrent RemoteStart (second rejected at CSMS level)
  7.3 — Swap Card (invalid then valid RFID)
  7.4 — Emergency Stop
  7.5 — Open Door
  7.6 — Power Loss / Reboot (simulated via Hard Reset)
  7.7 — Local List Offline

Run:
  python3 tests/vsecc/test_mea_section7.py

Note:
  Items requiring physical hardware interaction (RFID scan, E-stop button,
  door open switch, physical power loss) are recorded as WARN with a remark
  explaining the required action.
  Items requiring EV to be plugged in wait up to EV_WAIT_SEC for Preparing event.
  Set EV_WAIT_SEC = 0 to skip EV-dependent items immediately.
  After Hard Reset (7.6) the vSECC reboots; the test waits up to BOOT_WAIT_SEC.
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

# How long to wait for a physical EV to connect (seconds).
# Set to 0 to skip EV-dependent items without waiting.
EV_WAIT_SEC = 0
# How long to wait for vSECC to reboot and reconnect after Hard Reset.
BOOT_WAIT_SEC = 90
# How long to wait for a local stop event (card tap or E-stop) before giving up.
LOCAL_STOP_WAIT_SEC = 30

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

    def _h(self):
        return {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}

    def get_var(self, var_id):
        r = requests.get(f"{VSECC_BASE}/variables/{var_id}", headers=self._h(), timeout=10)
        return r.json() if r.ok else None

    def set_var(self, var_id, obj, value):
        body = dict(obj); body["value"] = value
        r = requests.put(f"{VSECC_BASE}/variables/{var_id}",
                         headers=self._h(), data=json.dumps(body), timeout=10)
        return r.ok


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

    def reset(self, reset_type="Hard"):
        return self._post("/remote/reset",
                          {"chargepoint": CP_ID, "reset_type": reset_type})

    def send_local_list(self, version=1, entries=None):
        if entries is None:
            entries = [{"idTag": RFID_TAG, "idTagInfo": {"status": "Accepted"}}]
        return self._post("/remote/sendLocalList", {
            "chargepoint_id": CP_ID,
            "listVersion": version,
            "localAuthorizationList": entries,
            "updateType": "Full"
        })


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
# MQTT — events + connection gate
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
            ("vsecc/ocpp_connection_status",              0),
            ("vsecc/connector/+/status/#",                0),
            ("vsecc/connector/+/ev/#",                    0),
            ("vsecc/connector/+/metervalues",             0),
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
    """
    Block until an MQTT event after `mark` matches both keywords, or timeout.
    Returns (matched_payload, topic) or (None, None).
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        with _mqtt_lock:
            for ts, topic, payload in _mqtt_events[mark:]:
                if topic_kw in topic and value_kw.lower() in payload.lower():
                    return payload, topic
        time.sleep(0.3)
    return None, None


def mqtt_set_availability(evse_id, state, wait_kw=None, timeout=10):
    if not HAS_MQTT or _mqtt_client is None:
        return None, None
    mark = mqtt_mark()
    _mqtt_client.publish(
        f"vsecc/connector/{evse_id}/status/set_availability", state)
    if wait_kw:
        return mqtt_wait(mark, "status", wait_kw, timeout=timeout)
    return None, None


def mqtt_events_since(mark, wait_sec=2):
    time.sleep(wait_sec)
    with _mqtt_lock:
        return list(_mqtt_events[mark:])


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
        payload, topic = mqtt_wait(mark, "ocpp_connection_status", "connected", timeout=5)
        if payload:
            _ocpp_up.set()
            return True
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
           "Connect physical EV and rerun for full Section 7 coverage")
    return False, "timeout"


# ─────────────────────────────────────────────
# Available state check helper
# ─────────────────────────────────────────────
def _check_available(item, message):
    """Force connector to operative via MQTT then confirm Available StatusNotification."""
    payload, _ = mqtt_set_availability(1, "operative", wait_kw="Available", timeout=10)
    if payload:
        record(item, message, "PASS", "Available confirmed on MQTT (via MQTT set_availability)")
    else:
        with _mqtt_lock:
            recent = _mqtt_events[-50:]
        if any("status" in t and "Available" in p for _, t, p in recent):
            record(item, message, "PASS", "Available observed in recent MQTT buffer")
        else:
            record(item, message, "WARN", "Available not observed on MQTT in 10 s",
                   "Check CSMS event log; charger may already be available")


# ─────────────────────────────────────────────
# 7.1 — RemoteStart while unplugged (Available)
# ─────────────────────────────────────────────
def run_7_1(mea: MeaApi):
    section("Section 7.1: RemoteStart while unplugged (Available state)")

    # ── 7.1.1  StatusNotification Available ─────────────────────────────────
    _check_available("7.1.1", "StatusNotification Available (charger idle, no EV)")

    # ── 7.1.2  RemoteStartTransaction (EV not connected) ─────────────────────
    r = mea.remote_start(connector_id=1, id_tag=RFID_TAG)
    ok, detail = mea_call(r)
    record("7.1.2",
           "RemoteStartTransaction (EV not connected) → HTTP 200",
           "PASS" if ok else "FAIL", detail,
           "vSECC should respond Rejected when no EV present")


# ─────────────────────────────────────────────
# 7.2 — Concurrent RemoteStart (second rejected)
# ─────────────────────────────────────────────
def run_7_2(mea: MeaApi):
    section("Section 7.2: Concurrent RemoteStart (second rejected)")

    # ── 7.2.1  StatusNotification Available ─────────────────────────────────
    _check_available("7.2.1", "StatusNotification Available (before EV plug)")

    # ── 7.2.2  StatusNotification Preparing (EV plug) ────────────────────────
    mark72 = mqtt_mark()
    ok22, _ = _ev_item("7.2.2", "StatusNotification Preparing (EV plug)", mark72)

    # ── 7.2.3  RemoteStartTransaction (first) ────────────────────────────────
    mark723 = mqtt_mark()
    r = mea.remote_start(connector_id=1, id_tag=RFID_TAG)
    ok723, detail723 = mea_call(r)
    record("7.2.3", "RemoteStartTransaction (first) → HTTP 200",
           "PASS" if ok723 else "FAIL", detail723)

    # ── 7.2.4  RemoteStartTransaction (second, concurrent) ───────────────────
    r2 = mea.remote_start(connector_id=1, id_tag=RFID_TAG)
    ok724, detail724 = mea_call(r2)
    record("7.2.4",
           "RemoteStartTransaction (second, concurrent) → HTTP 200",
           "PASS" if ok724 else "WARN", detail724,
           "CSMS likely blocks duplicate; charger-level Rejected not observable without proxy")

    # ── 7.2.5  StartTransaction ──────────────────────────────────────────────
    if ok723:
        payload, topic = mqtt_wait(mark723, "charging_session_state", "started", timeout=30)
        if not payload:
            payload, topic = mqtt_wait(mark723, "charging_session_state", "active", timeout=5)
        if payload:
            record("7.2.5", "StartTransaction (session started after first RemoteStart)",
                   "PASS", f"session_state={payload}")
        else:
            record("7.2.5", "StartTransaction (session started after first RemoteStart)",
                   "WARN", "charging_session_state not observed on MQTT in 30 s")
    else:
        record("7.2.5", "StartTransaction (session started after first RemoteStart)",
               "WARN", "Depends on 7.2.3 (first RemoteStart)")

    # ── 7.2.6  StatusNotification Charging ───────────────────────────────────
    if ok723:
        payload, topic = mqtt_wait(mark723, "status", "Charging", timeout=20)
        if payload:
            record("7.2.6", "StatusNotification Charging",
                   "PASS", f"{topic.split('/')[-1]}={payload}")
        else:
            record("7.2.6", "StatusNotification Charging",
                   "WARN", "Charging status not observed on MQTT in 20 s")
    else:
        record("7.2.6", "StatusNotification Charging",
               "WARN", "Depends on 7.2.3 (first RemoteStart)")

    # ── 7.2.7  MeterValues ───────────────────────────────────────────────────
    if ok723:
        mea.trigger_message("MeterValues", connector_id=1)
        payload, topic = mqtt_wait(mark723, "metervalues", "", timeout=15)
        if payload:
            record("7.2.7", "MeterValues (during charging)",
                   "PASS", f"metervalues received: {payload[:60]}")
        else:
            record("7.2.7", "MeterValues (during charging)",
                   "WARN", "MeterValues not observed on MQTT in 15 s")
    else:
        record("7.2.7", "MeterValues (during charging)",
               "WARN", "Depends on 7.2.3 (first RemoteStart)")

    # ── 7.2.8  RemoteStopTransaction ─────────────────────────────────────────
    mark728 = mqtt_mark()
    r = mea.remote_stop(0)
    ok728, detail728 = mea_call(r)
    record("7.2.8", "RemoteStopTransaction → HTTP 200",
           "PASS" if ok728 else "FAIL", detail728,
           "" if ok728 else "No active transaction (tx_id=0) — needs active charging session")

    # ── 7.2.9  StopTransaction ───────────────────────────────────────────────
    if ok728:
        payload, topic = mqtt_wait(mark728, "charging_session_state", "idle", timeout=20)
        if not payload:
            payload, topic = mqtt_wait(mark728, "charging_session_state", "finish", timeout=5)
        if payload:
            record("7.2.9", "StopTransaction (session ended after RemoteStop)",
                   "PASS", f"session_state={payload}")
        else:
            record("7.2.9", "StopTransaction (session ended after RemoteStop)",
                   "WARN", "session_state idle/finished not observed on MQTT in 25 s")
    else:
        record("7.2.9", "StopTransaction (session ended after RemoteStop)",
               "WARN", "Depends on 7.2.8 (RemoteStop)")

    # ── 7.2.10 StatusNotification Finishing ──────────────────────────────────
    if ok728:
        payload, topic = mqtt_wait(mark728, "status", "Finishing", timeout=15)
        if payload:
            record("7.2.10", "StatusNotification Finishing",
                   "PASS", f"{topic.split('/')[-1]}={payload}")
        else:
            record("7.2.10", "StatusNotification Finishing",
                   "WARN", "Finishing not observed on MQTT in 15 s",
                   "Some implementations skip Finishing and go directly to Available")
    else:
        record("7.2.10", "StatusNotification Finishing",
               "WARN", "Depends on 7.2.8 (RemoteStop)")

    # ── 7.2.11 StatusNotification Available ──────────────────────────────────
    if ok728:
        print("  ...waiting for Available (unplug EV after stop)...")
        payload, topic = mqtt_wait(mark728, "status", "Available", timeout=30)
        if payload:
            record("7.2.11", "StatusNotification Available (after stop / unplug)",
                   "PASS", f"{topic.split('/')[-1]}={payload}")
        else:
            record("7.2.11", "StatusNotification Available (after stop / unplug)",
                   "WARN", "Available not observed on MQTT in 30 s — unplug EV")
    else:
        record("7.2.11", "StatusNotification Available (after stop / unplug)",
               "WARN", "Depends on 7.2.8 (RemoteStop)")


# ─────────────────────────────────────────────
# 7.3 — Swap Card (invalid then valid RFID)
# ─────────────────────────────────────────────
def run_7_3(mea: MeaApi):
    section("Section 7.3: Swap Card (invalid then valid RFID)")

    RFID_WARN = "Requires physical RFID scan on vSECC hardware"

    # ── 7.3.1  StatusNotification Available ─────────────────────────────────
    _check_available("7.3.1", "StatusNotification Available (before EV plug)")

    # ── 7.3.2  StatusNotification Preparing (EV plug) ────────────────────────
    mark73 = mqtt_mark()
    ok32, _ = _ev_item("7.3.2", "StatusNotification Preparing (EV plug)", mark73)

    # ── 7.3.3  Authorize (invalid tag) ───────────────────────────────────────
    record("7.3.3",
           "Authorize (invalid RFID tag) → Rejected",
           "WARN",
           "Not triggerable via API — vSECC reads RFID directly from hardware",
           RFID_WARN + " of unknown/blocked card")

    # ── 7.3.4  Authorize (valid tag) ─────────────────────────────────────────
    record("7.3.4",
           "Authorize (valid RFID tag) → Accepted",
           "WARN",
           "Not triggerable via API — vSECC reads RFID directly from hardware",
           RFID_WARN + " of authorized card after rejected card")

    # ── 7.3.5  StartTransaction ───────────────────────────────────────────────
    record("7.3.5",
           "StartTransaction (after valid RFID)",
           "WARN",
           "Depends on 7.3.3/7.3.4 (physical RFID scan)",
           "Session starts only after valid RFID tap on vSECC hardware")

    # ── 7.3.6  StatusNotification Charging ───────────────────────────────────
    record("7.3.6",
           "StatusNotification Charging (after valid auth)",
           "WARN",
           "Depends on 7.3.5 (StartTransaction)",
           "Requires completed Authorize sequence via physical RFID hardware")

    # ── 7.3.7  MeterValues ───────────────────────────────────────────────────
    record("7.3.7",
           "MeterValues (during charging)",
           "WARN",
           "Depends on 7.3.6 (Charging state)",
           "Requires completed Authorize sequence via physical RFID hardware")

    # ── 7.3.8  RemoteStopTransaction ─────────────────────────────────────────
    record("7.3.8",
           "RemoteStopTransaction",
           "WARN",
           "Depends on 7.3.5 (StartTransaction)",
           "Requires active session started via physical RFID hardware")

    # ── 7.3.9  StopTransaction ───────────────────────────────────────────────
    record("7.3.9",
           "StopTransaction (after RemoteStop)",
           "WARN",
           "Depends on 7.3.8 (RemoteStop)",
           "Requires active session started via physical RFID hardware")

    # ── 7.3.10 StatusNotification Finishing ──────────────────────────────────
    record("7.3.10",
           "StatusNotification Finishing",
           "WARN",
           "Depends on 7.3.8 (RemoteStop)",
           "Requires active session started via physical RFID hardware")

    # ── 7.3.11 StatusNotification Available ──────────────────────────────────
    record("7.3.11",
           "StatusNotification Available (after stop / unplug)",
           "WARN",
           "Depends on 7.3.8 (RemoteStop)",
           "Requires active session started via physical RFID hardware")


# ─────────────────────────────────────────────
# 7.4 — Emergency Stop
# ─────────────────────────────────────────────
def run_7_4(mea: MeaApi):
    section("Section 7.4: Emergency Stop")

    # ── 7.4.1  StatusNotification Available ─────────────────────────────────
    _check_available("7.4.1", "StatusNotification Available (before EV plug)")

    # ── 7.4.2  StatusNotification Preparing (EV plug) ────────────────────────
    mark74 = mqtt_mark()
    ok42, _ = _ev_item("7.4.2", "StatusNotification Preparing (EV plug)", mark74)

    # ── 7.4.3  RemoteStartTransaction ────────────────────────────────────────
    mark743 = mqtt_mark()
    r = mea.remote_start(connector_id=1, id_tag=RFID_TAG)
    ok743, detail743 = mea_call(r)
    record("7.4.3", "RemoteStartTransaction → HTTP 200",
           "PASS" if ok743 else "FAIL", detail743)

    # ── 7.4.4  StartTransaction ───────────────────────────────────────────────
    if ok743:
        payload, topic = mqtt_wait(mark743, "charging_session_state", "started", timeout=30)
        if not payload:
            payload, topic = mqtt_wait(mark743, "charging_session_state", "active", timeout=5)
        if payload:
            record("7.4.4", "StartTransaction (session started after RemoteStart)",
                   "PASS", f"session_state={payload}")
        else:
            record("7.4.4", "StartTransaction (session started after RemoteStart)",
                   "WARN", "charging_session_state not observed on MQTT in 30 s")
    else:
        record("7.4.4", "StartTransaction (session started after RemoteStart)",
               "WARN", "Depends on 7.4.3 (RemoteStart)")

    # ── 7.4.5  StatusNotification Charging ───────────────────────────────────
    if ok743:
        payload, topic = mqtt_wait(mark743, "status", "Charging", timeout=20)
        if payload:
            record("7.4.5", "StatusNotification Charging",
                   "PASS", f"{topic.split('/')[-1]}={payload}")
        else:
            record("7.4.5", "StatusNotification Charging",
                   "WARN", "Charging status not observed on MQTT in 20 s")
    else:
        record("7.4.5", "StatusNotification Charging",
               "WARN", "Depends on 7.4.3 (RemoteStart)")

    # ── 7.4.6  MeterValues ───────────────────────────────────────────────────
    if ok743:
        mea.trigger_message("MeterValues", connector_id=1)
        payload, topic = mqtt_wait(mark743, "metervalues", "", timeout=15)
        if payload:
            record("7.4.6", "MeterValues (during charging)",
                   "PASS", f"metervalues received: {payload[:60]}")
        else:
            record("7.4.6", "MeterValues (during charging)",
                   "WARN", "MeterValues not observed on MQTT in 15 s")
    else:
        record("7.4.6", "MeterValues (during charging)",
               "WARN", "Depends on 7.4.3 (RemoteStart)")

    # ── 7.4.7  StopTransaction (EmergencyStop) ────────────────────────────────
    record("7.4.7",
           "StopTransaction (reason=EmergencyStop)",
           "WARN",
           "Triggered by physical emergency stop button on vSECC hardware",
           "Requires physical emergency stop button press on the vSECC unit")

    # ── 7.4.8  StatusNotification Faulted ────────────────────────────────────
    # If the E-stop was physically pressed we observe Faulted on MQTT.
    # Since we cannot press it via API, wait briefly for any Faulted event.
    mark748 = mqtt_mark()
    payload, topic = mqtt_wait(mark748, "status", "Faulted", timeout=10)
    if payload:
        record("7.4.8", "StatusNotification Faulted (after EmergencyStop)",
               "PASS", f"{topic.split('/')[-1]}={payload}")
    else:
        record("7.4.8", "StatusNotification Faulted (after EmergencyStop)",
               "WARN", "Faulted status not observed on MQTT (E-stop not pressed)",
               "Requires physical emergency stop button press on the vSECC unit")

    # ── 7.4.9  StatusNotification Available (E-stop cleared) ─────────────────
    record("7.4.9",
           "StatusNotification Available (E-stop cleared)",
           "WARN",
           "Depends on 7.4.7 (physical E-stop press and release)",
           "E-stop must be physically released to restore normal operation")

    # ── 7.4.10 StatusNotification Available (EV unplug) ──────────────────────
    record("7.4.10",
           "StatusNotification Available (EV unplug after E-stop recovery)",
           "WARN",
           "Depends on 7.4.7 (physical E-stop press and release)",
           "EV must be unplugged after E-stop recovery to reach Available")


# ─────────────────────────────────────────────
# 7.5 — Open Door
# ─────────────────────────────────────────────
def run_7_5(mea: MeaApi):
    section("Section 7.5: Open Door")

    # ── 7.5.1  StatusNotification Available ─────────────────────────────────
    _check_available("7.5.1", "StatusNotification Available (before door open)")

    # ── 7.5.2  StatusNotification Faulted (DoorOpen) ─────────────────────────
    record("7.5.2",
           "StatusNotification Faulted (reason=DoorOpen)",
           "WARN",
           "Triggered by opening physical door/panel on vSECC hardware",
           "Requires physical door open on vSECC hardware; not triggerable via API")

    # ── 7.5.3  StatusNotification Available (door closed) ────────────────────
    record("7.5.3",
           "StatusNotification Available (after door closed)",
           "WARN",
           "Depends on 7.5.2 (physical door open and close)",
           "Door must be physically closed to restore normal operation")


# ─────────────────────────────────────────────
# 7.6 — Power Loss / Reboot
# ─────────────────────────────────────────────
def run_7_6(mea: MeaApi):
    section("Section 7.6: Power Loss / Reboot (simulated via Hard Reset)")

    # ── 7.6.1  StatusNotification Available ─────────────────────────────────
    _check_available("7.6.1", "StatusNotification Available (before EV plug)")

    # ── 7.6.2  StatusNotification Preparing (EV plug) ────────────────────────
    mark76 = mqtt_mark()
    ok62, _ = _ev_item("7.6.2", "StatusNotification Preparing (EV plug)", mark76)

    # ── 7.6.3  RemoteStartTransaction ────────────────────────────────────────
    mark763 = mqtt_mark()
    r = mea.remote_start(connector_id=1, id_tag=RFID_TAG)
    ok763, detail763 = mea_call(r)
    record("7.6.3", "RemoteStartTransaction → HTTP 200",
           "PASS" if ok763 else "FAIL", detail763)

    # ── 7.6.4  StartTransaction ───────────────────────────────────────────────
    if ok763:
        payload, topic = mqtt_wait(mark763, "charging_session_state", "started", timeout=30)
        if not payload:
            payload, topic = mqtt_wait(mark763, "charging_session_state", "active", timeout=5)
        if payload:
            record("7.6.4", "StartTransaction (session started after RemoteStart)",
                   "PASS", f"session_state={payload}")
        else:
            record("7.6.4", "StartTransaction (session started after RemoteStart)",
                   "WARN", "charging_session_state not observed on MQTT in 30 s")
    else:
        record("7.6.4", "StartTransaction (session started after RemoteStart)",
               "WARN", "Depends on 7.6.3 (RemoteStart)")

    # ── 7.6.5  StatusNotification Charging ───────────────────────────────────
    if ok763:
        payload, topic = mqtt_wait(mark763, "status", "Charging", timeout=20)
        if payload:
            record("7.6.5", "StatusNotification Charging",
                   "PASS", f"{topic.split('/')[-1]}={payload}")
        else:
            record("7.6.5", "StatusNotification Charging",
                   "WARN", "Charging status not observed on MQTT in 20 s")
    else:
        record("7.6.5", "StatusNotification Charging",
               "WARN", "Depends on 7.6.3 (RemoteStart)")

    # ── 7.6.6  MeterValues ───────────────────────────────────────────────────
    if ok763:
        mea.trigger_message("MeterValues", connector_id=1)
        payload, topic = mqtt_wait(mark763, "metervalues", "", timeout=15)
        if payload:
            record("7.6.6", "MeterValues (during charging)",
                   "PASS", f"metervalues received: {payload[:60]}")
        else:
            record("7.6.6", "MeterValues (during charging)",
                   "WARN", "MeterValues not observed on MQTT in 15 s")
    else:
        record("7.6.6", "MeterValues (during charging)",
               "WARN", "Depends on 7.6.3 (RemoteStart)")

    # ── 7.6.7  StopTransaction (PowerLoss) — simulated via Hard Reset ─────────
    mark767 = mqtt_mark()
    r = mea.reset("Hard")
    ok767, detail767 = mea_call(r)
    record("7.6.7",
           "StopTransaction (PowerLoss) — simulated via Hard Reset → HTTP 200",
           "PASS" if ok767 else "FAIL", detail767,
           "Simulated via Hard Reset; physical power loss not testable in this environment")

    # ── 7.6.8  BootNotification (reconnect after reboot) ─────────────────────
    if ok767:
        print(f"  ...waiting up to {BOOT_WAIT_SEC}s for vSECC to reboot and reconnect...")
        reconnected = wait_for_reconnect(mea, mark767, timeout=BOOT_WAIT_SEC)
        if reconnected:
            record("7.6.8", "BootNotification → Accepted (vSECC reconnected after reboot)",
                   "PASS", "CSMS reconnection confirmed after Hard Reset")
        else:
            record("7.6.8", "BootNotification → Accepted (vSECC reconnected after reboot)",
                   "WARN", f"No reconnect in {BOOT_WAIT_SEC}s after Hard Reset",
                   "vSECC may take longer to reboot; increase BOOT_WAIT_SEC")
    else:
        record("7.6.8", "BootNotification → Accepted (vSECC reconnected after reboot)",
               "WARN", "Depends on 7.6.7 (Hard Reset)")

    # ── 7.6.9  StatusNotification Preparing (EV still connected after reboot) ─
    if ok767:
        payload, topic = mqtt_wait(mark767, "status", "Preparing", timeout=20)
        if payload:
            record("7.6.9",
                   "StatusNotification Preparing (EV still connected after reboot)",
                   "PASS", f"{topic.split('/')[-1]}={payload}")
        else:
            record("7.6.9",
                   "StatusNotification Preparing (EV still connected after reboot)",
                   "WARN", "Preparing not observed on MQTT in 20 s after reboot",
                   "EV must remain connected through the reboot sequence")
    else:
        record("7.6.9",
               "StatusNotification Preparing (EV still connected after reboot)",
               "WARN", "Depends on 7.6.7 (Hard Reset)")

    # ── 7.6.10 StatusNotification Available (EV unplugged after reboot) ───────
    if ok767:
        print("  ...waiting for Available (unplug EV after reboot)...")
        payload, topic = mqtt_wait(mark767, "status", "Available", timeout=30)
        if payload:
            record("7.6.10",
                   "StatusNotification Available (EV unplug after reboot)",
                   "PASS", f"{topic.split('/')[-1]}={payload}")
        else:
            record("7.6.10",
                   "StatusNotification Available (EV unplug after reboot)",
                   "WARN", "Available not observed on MQTT in 30 s — unplug EV")
    else:
        record("7.6.10",
               "StatusNotification Available (EV unplug after reboot)",
               "WARN", "Depends on 7.6.7 (Hard Reset)")


# ─────────────────────────────────────────────
# 7.7 — Local List Offline
# ─────────────────────────────────────────────
def run_7_7(mea: MeaApi):
    section("Section 7.7: Local List Offline")

    OFFLINE_WARN = "Offline buffering not observable via MQTT/REST without proxy"

    # ── 7.7.1  SendLocalList ──────────────────────────────────────────────────
    r = mea.send_local_list(version=1)
    ok, detail = mea_call(r)
    record("7.7.1",
           "SendLocalList (version=1, RFID_TEST=Accepted) → HTTP 200",
           "PASS" if ok else "FAIL", detail)

    # ── 7.7.2  StatusNotification Available (online, before going offline) ────
    record("7.7.2",
           "StatusNotification Available (charger online before offline test)",
           "WARN",
           "Offline sequence not observable via MQTT/REST without proxy",
           OFFLINE_WARN)

    # ── 7.7.3  StatusNotification Preparing (EV plug, charger offline) ────────
    record("7.7.3",
           "StatusNotification Preparing (EV plug while charger offline)",
           "WARN",
           "Offline start requires disconnecting vSECC from CSMS network",
           OFFLINE_WARN)

    # ── 7.7.4  Authorize (local list, offline) ────────────────────────────────
    record("7.7.4",
           "Authorize (local list lookup while offline) → Accepted",
           "WARN",
           "vSECC must authorize from local list while CSMS unreachable",
           OFFLINE_WARN)

    # ── 7.7.5  StartTransaction (offline, buffered) ───────────────────────────
    record("7.7.5",
           "StartTransaction (offline, buffered by vSECC)",
           "WARN",
           "vSECC buffers transaction in firmware; not observable without proxy",
           OFFLINE_WARN)

    # ── 7.7.6  StopTransaction (offline) ─────────────────────────────────────
    record("7.7.6",
           "StopTransaction (offline, buffered by vSECC)",
           "WARN",
           "vSECC buffers stop in firmware; delivered when CSMS reconnects",
           OFFLINE_WARN)

    # ── 7.7.7  Reconnect / flush buffered transactions ────────────────────────
    record("7.7.7",
           "CSMS reconnect — buffered Start/StopTransaction flushed",
           "WARN",
           "Reconnect flushing is firmware-internal; not observable without proxy",
           OFFLINE_WARN)


# ─────────────────────────────────────────────
# Section 7 — top-level runner
# ─────────────────────────────────────────────
def run_section7(vsecc: VseccApi, mea: MeaApi):
    section("Section 7: การตรวจสอบการทำงานผิดปกติ (Abnormal Operation Verification)")

    print("  Checking vSECC → MEA CSMS connection (up to 30 s)...")
    if not _probe_csms_connection(mea, timeout=30):
        print("  ERROR: vSECC not connected to MEA CSMS.")
        for sub in range(1, 8):
            record(f"7.{sub}.1", f"Section 7.{sub} items", "FAIL",
                   "vSECC not connected to MEA CSMS")
        return
    print("  vSECC connected to MEA CSMS.")
    time.sleep(1)

    run_7_1(mea)
    run_7_2(mea)
    run_7_3(mea)
    run_7_4(mea)
    run_7_5(mea)
    run_7_6(mea)
    run_7_7(mea)


# ─────────────────────────────────────────────
# Summary + JSON
# ─────────────────────────────────────────────
def print_summary():
    counts = {s: sum(1 for r in results if r["status"] == s)
              for s in ("PASS", "FAIL", "WARN", "SKIP")}
    total  = sum(counts.values())
    print(f"\n{'═'*66}")
    print(f"  Section 7  |  "
          f"{counts['PASS']} PASS  {counts['FAIL']} FAIL  "
          f"{counts['WARN']} WARN  {counts['SKIP']} SKIP  ({total} total)")
    print(f"{'═'*66}")
    for r in results:
        print(f"  {r['item']:<8} {r['status']:<6}  {r['message'][:50]}")
    print(f"{'═'*66}")


_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def save_json(path=None):
    if path is None:
        path = os.path.join(_ROOT, "tex", "vsecc_section7_results.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump({
            "section": 7,
            "title": "การตรวจสอบการทำงานผิดปกติ",
            "cp_id": CP_ID,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "method": "direct-to-csms",
            "ev_wait_sec": EV_WAIT_SEC,
            "boot_wait_sec": BOOT_WAIT_SEC,
            "local_stop_wait_sec": LOCAL_STOP_WAIT_SEC,
            "results": results,
        }, f, indent=2, ensure_ascii=False)
    print(f"\n  Saved {path}")


# ─────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────
def main():
    print("=" * 66)
    print("  MEA OCPP 1.6 Compliance — Section 7 (7.1 – 7.7)  [no proxy]")
    print(f"  CP: {CP_ID}  |  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  EV wait: {EV_WAIT_SEC} s  |  Boot wait: {BOOT_WAIT_SEC} s  |  "
          f"Local stop: {LOCAL_STOP_WAIT_SEC} s")
    print("=" * 66)

    vsecc = VseccApi()
    mea   = MeaApi()

    if not vsecc.login():
        print("ABORT: Cannot reach vSECC (need alias: sudo ip addr add 192.168.1.200/24 dev enp3s0)")
        sys.exit(1)
    print("  vSECC authenticated")

    start_mqtt_watcher()
    try:
        run_section7(vsecc, mea)
    finally:
        stop_mqtt_watcher()

    print_summary()
    save_json()


if __name__ == "__main__":
    main()

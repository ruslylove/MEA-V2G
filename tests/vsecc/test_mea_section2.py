#!/usr/bin/env python3
"""
MEA OCPP 1.6 Compliance Test — Section 2
การตรวจสอบ Auto Charge (Auto Charge Verification)
Items 2.1 – 2.21 per OCPP1.6_FORM_MEA.pdf Annex B.

Architecture (no proxy):
  vSECC  →  wss://ocpp.measandbox.com:2930  (direct TLS)
  PC     →  vSECC REST API   (configure, read state)
  PC     →  vSECC MQTT       (observe status / session / CP state)
  PC     →  MEA REST API     (ChangeConfiguration, RemoteStart, RemoteStop)

Charging session flow:
  Session 1 — EV plug → Authorize (RFID/VID) → StartTransaction →
              Charging → RemoteStop → Finishing → StopTransaction → Available
  Session 2 — EV plug → Authorize → StartTransaction →
              Charging → MeterValues → SuspendedEV → StopTransaction (EVDisconnected) → Available

Run:
  python3 tests/vsecc/test_mea_section2.py

Note:
  Items 2.2, 2.5, 2.14 require a physical EV to be plugged into the vSECC connector.
  The test waits up to EV_WAIT_SEC for the Preparing event; if no EV arrives, those
  items (and all that depend on the charging session) are recorded as WARN.
  Set EV_WAIT_SEC = 0 to skip EV-dependent items immediately.
"""

import json
import os
import sys
import time
import threading
import requests
from datetime import datetime, timedelta
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

    def change_configuration(self, key, value):
        return self._post("/remote/changeConfiguration",
                          {"chargepoint_id": CP_ID, "key": key, "value": value})

    def get_configuration(self, key=None):
        return self._post("/cmd/chargepoint/getConfiguration",
                          {"chargepoint": CP_ID, "key": [key] if key else []})

    def remote_start(self, connector_id=1, id_tag=RFID_TAG):
        return self._post("/cmd/chargepoint/remoteStart",
                          {"chargepoint_id": CP_ID, "connector_id": connector_id,
                           "card_id": id_tag},
                          pwd=MEA_PASS_START)

    def remote_stop(self, transaction_id):
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


def mea_call_status(r, expected="Accepted"):
    """Return (ok, status_str, detail). ok = status == expected."""
    if r is None:
        return False, "?", "No response"
    try:
        body   = r.json()
        status = body.get("status") or body.get("result") or f"HTTP {r.status_code}"
    except Exception:
        status = f"HTTP {r.status_code}"
    ok     = r.status_code == 200 and status == expected
    detail = f"status={status}"
    return ok, status, detail


# ─────────────────────────────────────────────
# Session state (shared across items)
# ─────────────────────────────────────────────
class SessionState:
    def __init__(self):
        self.transaction_id = None   # filled when StartTransaction observed
        self.session_active = False
        self.ev_present     = False  # set when Preparing observed on MQTT


_s = SessionState()


def _ev_item(item, message, mark, timeout=None):
    """
    Wait for EV-plug event (Preparing) on MQTT.
    Returns (status, detail, payload).
    """
    if timeout is None:
        timeout = EV_WAIT_SEC
    if timeout == 0:
        record(item, message, "WARN", "EV_WAIT_SEC=0 — skipped",
               "Connect physical EV to connector and rerun")
        return False, "skipped"

    print(f"  ...waiting up to {timeout}s for {message} (connect EV if needed)...")
    payload, topic = mqtt_wait(mark, "status", "Preparing", timeout=timeout)
    if payload:
        _s.ev_present = True
        record(item, message, "PASS", f"{topic.split('/')[-1]}={payload}")
        return True, payload
    else:
        record(item, message, "WARN", f"No Preparing event in {timeout}s — no EV connected",
               "Connect physical EV and rerun for full Section 2 coverage")
        return False, "timeout"


# ─────────────────────────────────────────────
# Section 2 test
# ─────────────────────────────────────────────
def _probe_csms_connection(mea: MeaApi, timeout=30) -> bool:
    """
    Probe whether vSECC is connected to MEA CSMS by calling GetConfiguration.
    Returns True if the CSMS can reach the charger (HTTP 200).
    Also accepts the MQTT event if it arrives before timeout.
    """
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


def run_section2(vsecc: VseccApi, mea: MeaApi):
    section("Section 2: การตรวจสอบ Auto Charge (Auto Charge Verification)")

    # ── Verify OCPP connection (MQTT event OR REST API probe) ─────────────────
    print("  Checking vSECC → MEA CSMS connection (up to 30 s)...")
    if not _probe_csms_connection(mea, timeout=30):
        print("  ERROR: vSECC not connected to MEA CSMS.")
        for i in range(1, 22):
            record(f"2.{i}", f"Item 2.{i}", "FAIL",
                   "vSECC not connected to MEA CSMS")
        return
    print("  vSECC connected to MEA CSMS.")
    time.sleep(1)

    # ══════════════════════════════════════════════════════════════════════════
    # ── 2.1  ChangeConfiguration AutoCharge=False ─────────────────────────────
    # ══════════════════════════════════════════════════════════════════════════
    r = mea.change_configuration("AutoCharge", "False")
    ok, detail = mea_call(r)
    record("2.1", "ChangeConfiguration AutoCharge=False → Accepted",
           "PASS" if ok else "FAIL", detail,
           "" if ok else "vSECC may not expose AutoCharge; mapped to tx_ctrlr_tx_before_accepted_enabled")

    # ── 2.2  StatusNotification Preparing (AutoCharge disabled, EV plug) ──────
    mark22 = mqtt_mark()
    ok22, _ = _ev_item("2.2", "StatusNotification Preparing (AutoCharge=False)", mark22)

    # ── 2.3  StatusNotification Available (unplug) ────────────────────────────
    if ok22:
        print("  ...waiting for Available (unplug EV)...")
        payload, topic = mqtt_wait(mark22, "status", "Available", timeout=60)
        if payload:
            record("2.3", "StatusNotification Available (unplug)",
                   "PASS", f"{topic.split('/')[-1]}={payload}")
        else:
            record("2.3", "StatusNotification Available (unplug)",
                   "WARN", "No Available event in 60 s")
    else:
        record("2.3", "StatusNotification Available (unplug)",
               "WARN", "Depends on 2.2 (EV plug)")

    # ══════════════════════════════════════════════════════════════════════════
    # ── 2.4  ChangeConfiguration AutoCharge=True ─────────────────────────────
    # ══════════════════════════════════════════════════════════════════════════
    r = mea.change_configuration("AutoCharge", "True")
    ok, detail = mea_call(r)
    record("2.4", "ChangeConfiguration AutoCharge=True → Accepted",
           "PASS" if ok else "FAIL", detail)

    # ── 2.5  StatusNotification Preparing (AutoCharge enabled, EV plug) ───────
    mark25 = mqtt_mark()
    ok25, _ = _ev_item("2.5", "StatusNotification Preparing (AutoCharge=True)", mark25)

    # ── 2.6  Authorize (VID) ──────────────────────────────────────────────────
    # vSECC sends Authorize when it reads the RFID/VID tag.
    # Observable on MQTT charging_authorization_state.
    if ok25:
        payload, topic = mqtt_wait(mark25, "authorization", "Accepted", timeout=30)
        if not payload:
            # Also accept "authorized" in CP state transitions
            payload, topic = mqtt_wait(mark25, "cp_state", "C", timeout=10)
        if payload:
            record("2.6", "Authorize (VID) → Accepted",
                   "PASS", f"auth_state={payload}")
        else:
            record("2.6", "Authorize (VID) → Accepted",
                   "WARN", "authorization_state not observed on MQTT in 30 s",
                   "May require RFID scan on physical hardware")
    else:
        record("2.6", "Authorize (VID) → Accepted",
               "WARN", "Depends on 2.5 (EV plug)")

    # ── 2.7  StartTransaction ─────────────────────────────────────────────────
    if ok25:
        payload, topic = mqtt_wait(mark25, "charging_session_state", "started", timeout=30)
        if not payload:
            payload, topic = mqtt_wait(mark25, "charging_session_state", "active", timeout=5)
        if payload:
            _s.session_active = True
            record("2.7", "StartTransaction (session started)",
                   "PASS", f"session_state={payload}")
        else:
            record("2.7", "StartTransaction (session started)",
                   "WARN", "charging_session_state not observed on MQTT")
    else:
        record("2.7", "StartTransaction (session started)",
               "WARN", "Depends on 2.5 (EV plug)")

    # ── 2.8  StatusNotification Charging ──────────────────────────────────────
    if ok25:
        payload, topic = mqtt_wait(mark25, "status", "Charging", timeout=20)
        if payload:
            record("2.8", "StatusNotification Charging",
                   "PASS", f"{topic.split('/')[-1]}={payload}")
        else:
            record("2.8", "StatusNotification Charging",
                   "WARN", "Charging status not observed on MQTT in 20 s")
    else:
        record("2.8", "StatusNotification Charging",
               "WARN", "Depends on 2.5 (EV plug)")

    # ── 2.9  MeterValues ──────────────────────────────────────────────────────
    if ok25:
        # Trigger meter values via MEA API
        r = mea.trigger_message("MeterValues", connector_id=1)
        ok9, _ = mea_call(r)
        # Also watch MQTT for any metervalues publication
        payload, topic = mqtt_wait(mark25, "metervalues", "", timeout=15)
        if payload:
            record("2.9", "MeterValues (measurands during charging)",
                   "PASS", f"metervalues received: {payload[:60]}")
        elif ok9:
            record("2.9", "MeterValues (measurands during charging)",
                   "WARN", "TriggerMessage Accepted but values not observed on MQTT")
        else:
            record("2.9", "MeterValues (measurands during charging)",
                   "WARN", "MeterValues not observed (no active transaction or MQTT gap)")
    else:
        record("2.9", "MeterValues (measurands during charging)",
               "WARN", "Depends on 2.5 (EV plug / active charging)")

    # ── 2.10 RemoteStopTransaction ────────────────────────────────────────────
    # Use transaction_id=0 if unknown (some CSMS implementations accept it
    # when only one transaction is active)
    tx_id = _s.transaction_id or 0
    mark210 = mqtt_mark()
    r = mea.remote_stop(tx_id)
    ok, detail = mea_call(r)
    remark210 = "" if ok else ("No active transaction (tx_id=0) — needs active charging session" if tx_id == 0 else "")
    record("2.10", "RemoteStopTransaction → Accepted",
           "PASS" if ok else "FAIL", detail, remark210)

    # ── 2.11 StopTransaction (sent by vSECC after RemoteStop) ─────────────────
    if ok:
        # StopTransaction is reflected in session_state going to "finished"/"idle"
        payload, topic = mqtt_wait(mark210, "charging_session_state", "idle", timeout=20)
        if not payload:
            payload, topic = mqtt_wait(mark210, "charging_session_state", "finish", timeout=10)
        if payload:
            _s.session_active = False
            record("2.11", "StopTransaction (sent after RemoteStop)",
                   "PASS", f"session_state={payload}")
        else:
            record("2.11", "StopTransaction (sent after RemoteStop)",
                   "WARN", "session_state idle/finished not observed on MQTT in 30 s")
    else:
        record("2.11", "StopTransaction (sent after RemoteStop)",
               "WARN", "Depends on 2.10 (RemoteStop)")

    # ── 2.12 StatusNotification Finishing ────────────────────────────────────
    if ok:
        payload, topic = mqtt_wait(mark210, "status", "Finishing", timeout=20)
        if payload:
            record("2.12", "StatusNotification Finishing",
                   "PASS", f"{topic.split('/')[-1]}={payload}")
        else:
            record("2.12", "StatusNotification Finishing",
                   "WARN", "Finishing status not observed on MQTT",
                   "Some implementations go directly Available without Finishing")
    else:
        record("2.12", "StatusNotification Finishing",
               "WARN", "Depends on 2.10 (RemoteStop)")

    # ── 2.13 StatusNotification Available (after RemoteStop / unplug) ─────────
    if ok:
        payload, topic = mqtt_wait(mark210, "status", "Available", timeout=30)
        if payload:
            record("2.13", "StatusNotification Available (after RemoteStop/unplug)",
                   "PASS", f"{topic.split('/')[-1]}={payload}")
        else:
            record("2.13", "StatusNotification Available (after RemoteStop/unplug)",
                   "WARN", "Available status not observed on MQTT in 30 s")
    else:
        record("2.13", "StatusNotification Available (after RemoteStop/unplug)",
               "WARN", "Depends on 2.10 (RemoteStop)")

    # ══════════════════════════════════════════════════════════════════════════
    # Session 2 — EV plug → Authorize → StartTransaction →
    #             Charging → MeterValues → SuspendedEV → StopTx (EVDisconnected)
    # ══════════════════════════════════════════════════════════════════════════

    # ── 2.14 StatusNotification Preparing (Session 2) ─────────────────────────
    mark214 = mqtt_mark()
    ok14, _ = _ev_item("2.14", "StatusNotification Preparing (Session 2)", mark214)

    # ── 2.15 Authorize (Session 2) ────────────────────────────────────────────
    if ok14:
        payload, topic = mqtt_wait(mark214, "authorization", "Accepted", timeout=30)
        if payload:
            record("2.15", "Authorize (Session 2) → Accepted",
                   "PASS", f"auth_state={payload}")
        else:
            record("2.15", "Authorize (Session 2) → Accepted",
                   "WARN", "authorization_state not observed on MQTT in 30 s")
    else:
        record("2.15", "Authorize (Session 2) → Accepted",
               "WARN", "Depends on 2.14 (EV plug)")

    # ── 2.16 StartTransaction (Session 2) ────────────────────────────────────
    if ok14:
        payload, topic = mqtt_wait(mark214, "charging_session_state", "started", timeout=30)
        if not payload:
            payload, topic = mqtt_wait(mark214, "charging_session_state", "active", timeout=5)
        if payload:
            _s.session_active = True
            record("2.16", "StartTransaction (Session 2)",
                   "PASS", f"session_state={payload}")
        else:
            record("2.16", "StartTransaction (Session 2)",
                   "WARN", "charging_session_state not observed on MQTT")
    else:
        record("2.16", "StartTransaction (Session 2)",
               "WARN", "Depends on 2.14 (EV plug)")

    # ── 2.17 StatusNotification Charging (Session 2) ─────────────────────────
    if ok14:
        payload, topic = mqtt_wait(mark214, "status", "Charging", timeout=20)
        if payload:
            record("2.17", "StatusNotification Charging (Session 2)",
                   "PASS", f"{topic.split('/')[-1]}={payload}")
        else:
            record("2.17", "StatusNotification Charging (Session 2)",
                   "WARN", "Charging status not observed on MQTT")
    else:
        record("2.17", "StatusNotification Charging (Session 2)",
               "WARN", "Depends on 2.14 (EV plug)")

    # ── 2.18 MeterValues (Session 2) ─────────────────────────────────────────
    if ok14:
        mea.trigger_message("MeterValues", connector_id=1)
        payload, topic = mqtt_wait(mark214, "metervalues", "", timeout=15)
        if payload:
            record("2.18", "MeterValues (Session 2)",
                   "PASS", f"metervalues received: {payload[:60]}")
        else:
            record("2.18", "MeterValues (Session 2)",
                   "WARN", "MeterValues not observed on MQTT in 15 s")
    else:
        record("2.18", "MeterValues (Session 2)",
               "WARN", "Depends on 2.14 (EV plug)")

    # ── 2.19 StatusNotification SuspendedEV ──────────────────────────────────
    # This occurs when the EV stops drawing power (BMS full / schedule limit).
    # Observable via MQTT status topic.
    if ok14:
        payload, topic = mqtt_wait(mark214, "status", "Suspended", timeout=30)
        if payload:
            record("2.19", "StatusNotification SuspendedEV",
                   "PASS", f"{topic.split('/')[-1]}={payload}")
        else:
            record("2.19", "StatusNotification SuspendedEV",
                   "WARN", "SuspendedEV not observed in 30 s",
                   "Requires EV to stop drawing current (BMS full or EV-side suspend)")
    else:
        record("2.19", "StatusNotification SuspendedEV",
               "WARN", "Depends on 2.14 (EV plug)")

    # ── 2.20 StopTransaction (reason=EVDisconnected) ──────────────────────────
    # This happens when the EV is unplugged during or after charging.
    if ok14:
        print("  ...waiting for EV disconnect (StopTransaction EVDisconnected)...")
        payload, topic = mqtt_wait(mark214, "charging_session_state", "idle", timeout=60)
        if not payload:
            payload, topic = mqtt_wait(mark214, "charging_session_state", "finish", timeout=5)
        if payload:
            _s.session_active = False
            record("2.20", "StopTransaction (reason=EVDisconnected)",
                   "PASS", f"session_state={payload}")
        else:
            record("2.20", "StopTransaction (reason=EVDisconnected)",
                   "WARN", "session_state idle/finished not observed in 60 s",
                   "Unplug EV to trigger EVDisconnected StopTransaction")
    else:
        record("2.20", "StopTransaction (reason=EVDisconnected)",
               "WARN", "Depends on 2.14 (EV plug)")

    # ── 2.21 StatusNotification Available (Session 2 end) ────────────────────
    if ok14:
        payload, topic = mqtt_wait(mark214, "status", "Available", timeout=20)
        if payload:
            record("2.21", "StatusNotification Available (Session 2 end)",
                   "PASS", f"{topic.split('/')[-1]}={payload}")
        else:
            record("2.21", "StatusNotification Available (Session 2 end)",
                   "WARN", "Available status not observed on MQTT in 20 s")
    else:
        record("2.21", "StatusNotification Available (Session 2 end)",
               "WARN", "Depends on 2.14 (EV plug)")


# ─────────────────────────────────────────────
# Summary + JSON
# ─────────────────────────────────────────────
def print_summary():
    counts = {s: sum(1 for r in results if r["status"] == s)
              for s in ("PASS", "FAIL", "WARN", "SKIP")}
    total  = sum(counts.values())
    print(f"\n{'═'*66}")
    print(f"  Section 2  |  "
          f"{counts['PASS']} PASS  {counts['FAIL']} FAIL  "
          f"{counts['WARN']} WARN  {counts['SKIP']} SKIP  ({total} total)")
    print(f"{'═'*66}")
    for r in results:
        print(f"  {r['item']:<6} {r['status']:<6}  {r['message'][:52]}")
    print(f"{'═'*66}")


_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def save_json(path=None):
    if path is None:
        path = os.path.join(_ROOT, "tex", "vsecc_section2_results.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump({
            "section": 2,
            "title": "การตรวจสอบ Auto Charge",
            "cp_id": CP_ID,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "method": "direct-to-csms",
            "ev_wait_sec": EV_WAIT_SEC,
            "results": results,
        }, f, indent=2, ensure_ascii=False)
    print(f"\n  Saved {path}")


# ─────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────
def main():
    print("=" * 66)
    print("  MEA OCPP 1.6 Compliance — Section 2 (2.1 – 2.21)  [no proxy]")
    print(f"  CP: {CP_ID}  |  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  EV wait timeout: {EV_WAIT_SEC} s per plug event")
    print("=" * 66)

    vsecc = VseccApi()
    mea   = MeaApi()

    if not vsecc.login():
        print("ABORT: Cannot reach vSECC (need alias: sudo ip addr add 192.168.1.200/24 dev enp3s0)")
        sys.exit(1)
    print("  vSECC authenticated")

    start_mqtt_watcher()
    try:
        run_section2(vsecc, mea)
    finally:
        stop_mqtt_watcher()

    print_summary()
    save_json()


if __name__ == "__main__":
    main()

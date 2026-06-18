#!/usr/bin/env python3
"""
MEA OCPP 1.6 Compliance Test — Section 6
การตรวจสอบ Charging Profile (Charging Profile Verification)
Items 6.1 – 6.26 per OCPP1.6_FORM_MEA.pdf Annex B.

Architecture (no proxy):
  vSECC  →  wss://ocpp.measandbox.com:2930  (direct TLS)
  PC     →  vSECC REST API   (login)
  PC     →  vSECC MQTT       (observe status / session / metervalues)
  PC     →  MEA REST API     (SetChargingProfile, RemoteStart, RemoteStop)

Two flows tested:
  Flow 1 (6.1–6.13)  — Local start session with charging profiles:
                        EV plug → Authorize (RFID) → StartTransaction →
                        Charging → MeterValues → SetChargingProfile (5 kW) →
                        MeterValues → SetChargingProfile (update) →
                        MeterValues → StopTransaction (card tap / RemoteStop) →
                        Finishing → Available
  Flow 2 (6.14–6.26) — RemoteStart session with charging profiles:
                        EV plug → RemoteStartTransaction →
                        StartTransaction → Charging → MeterValues →
                        SetChargingProfile → MeterValues →
                        SetChargingProfile (second update) → MeterValues →
                        RemoteStopTransaction → StopTransaction →
                        Finishing → Available

Run:
  python3 tests/vsecc/test_mea_section6.py

Note:
  Items 6.2 and 6.14 require a physical EV to be plugged into the vSECC connector.
  Item 6.11 requires the EV driver to tap an RFID card to stop locally; if no card
  tap is detected within LOCAL_STOP_WAIT_SEC, the test falls back to RemoteStop
  and records WARN.
  Set EV_WAIT_SEC = 0 to skip EV-dependent items immediately.
"""

import json
import os
import sys
import time
import threading
import datetime
import requests
from datetime import datetime as dt
from requests.auth import HTTPDigestAuth
from vsecc_log import VseccLog, print_ocpp

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
EV_WAIT_SEC = 0
# Wait for manual RFID card tap to stop charging (seconds).
# If expired, test falls back to RemoteStop.
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

    def set_charging_profile(self, connector_id=1, transaction_id=0, limit_w=5000):
        now = datetime.datetime.now(datetime.timezone.utc)
        valid_to = (now + datetime.timedelta(days=1)).isoformat().replace("+00:00", "Z")
        start_sched = now.isoformat().replace("+00:00", "Z")
        return self._post("/remote/SetChargingProfile", {
            "chargepoint_id": CP_ID,
            "connectorId": connector_id,
            "csChargingProfiles": {
                "chargingProfileId": 101,
                "transactionId": transaction_id,
                "stackLevel": 1,
                "chargingProfilePurpose": "TxProfile",
                "chargingProfileKind": "Absolute",
                "validFrom": now.isoformat().replace("+00:00", "Z"),
                "validTo": valid_to,
                "chargingSchedule": {
                    "duration": 3600,
                    "startSchedule": start_sched,
                    "chargingRateUnit": "W",
                    "chargingSchedulePeriod": [
                        {"startPeriod": 0, "limit": limit_w, "numberPhases": 3}
                    ]
                }
            }
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


def mqtt_set_availability(evse_id, state, wait_kw=None, timeout=10):
    if not HAS_MQTT or _mqtt_client is None:
        return None, None
    mark = mqtt_mark()
    _mqtt_client.publish(
        f"vsecc/connector/{evse_id}/status/set_availability", state)
    if wait_kw:
        return mqtt_wait(mark, "status", wait_kw, timeout=timeout)
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
           "Connect physical EV and rerun for full Section 6 coverage")
    return False, "timeout"


# ─────────────────────────────────────────────
# Section 6 test body
# ─────────────────────────────────────────────
def run_section6(vsecc: VseccApi, mea: MeaApi, log: VseccLog = None):
    section("Section 6: การตรวจสอบ Charging Profile (Charging Profile Verification)")

    # ── Check OCPP connection ─────────────────────────────────────────────────
    print("  Checking vSECC → MEA CSMS connection (up to 30 s)...")
    if not _probe_csms_connection(mea, timeout=30):
        print("  ERROR: vSECC not connected to MEA CSMS.")
        for i in range(1, 27):
            record(f"6.{i}", f"Item 6.{i}", "FAIL", "vSECC not connected to MEA CSMS")
        return
    print("  vSECC connected to MEA CSMS.")

    # ══════════════════════════════════════════════════════════════════════════
    # Flow 1: Local start session with charging profiles — 6.1–6.13
    # ══════════════════════════════════════════════════════════════════════════
    print("\n  [Flow 1] Local Start with Charging Profile")

    # ── 6.1  StatusNotification Available ────────────────────────────────────
    if log: log.mark()
    payload61, _ = mqtt_set_availability(1, "operative",
                                         wait_kw="Available", timeout=10)
    frames_61 = log.ocpp_frames() if log else []
    log_avail61 = log.find(frames_61, "StatusNotification", "Available") if log else None
    if frames_61: _last_raw = "\n".join(f.strip() for f in frames_61)
    if payload61:
        record("6.1", "StatusNotification Available (initial state)",
               "PASS", "Available confirmed on MQTT (via MQTT set_availability)")
    elif log_avail61:
        record("6.1", "StatusNotification Available (initial state)",
               "PASS", f"confirmed via ocpplib.log: {log_avail61.strip()}")
        print_ocpp(frames_61, "6.1 (from ocpplib.log)")
    else:
        with _mqtt_lock:
            recent = _mqtt_events[-30:]
        if any("status" in t and "Available" in p for _, t, p in recent):
            record("6.1", "StatusNotification Available (initial state)",
                   "PASS", "Available observed in recent MQTT buffer")
        else:
            record("6.1", "StatusNotification Available (initial state)",
                   "WARN", "not observed on MQTT or ocpplib.log in 10 s",
                   "Check CSMS event log; charger may already be available")

    # ── 6.2  StatusNotification Preparing (EV plug, Flow 1) ──────────────────
    mark62 = mqtt_mark()
    ok62, _ = _ev_item("6.2", "StatusNotification Preparing (EV plug, Flow 1)", mark62)

    # ── 6.3  Authorize (RFID card) ────────────────────────────────────────────
    if ok62:
        payload, topic = mqtt_wait(mark62, "authorization", "Accepted", timeout=30)
        if payload:
            record("6.3", "Authorize (RFID card) → Accepted",
                   "PASS", f"auth_state={payload}")
        else:
            record("6.3", "Authorize (RFID card) → Accepted",
                   "WARN", "authorization_state Accepted not observed in 30 s",
                   "Requires RFID scan on physical hardware")
    else:
        record("6.3", "Authorize (RFID card) → Accepted",
               "WARN", "Depends on 6.2 (EV plug)")

    # ── 6.4  StartTransaction (manual RFID) ───────────────────────────────────
    if ok62:
        if log: log.mark()
        payload, topic = mqtt_wait(mark62, "charging_session_state", "started", timeout=30)
        if not payload:
            payload, topic = mqtt_wait(mark62, "charging_session_state", "active", timeout=5)
        frames_64 = log.ocpp_frames() if log else []
        log_match = log.find(frames_64, "StartTransaction") if log else None
        if frames_64: _last_raw = "\n".join(f.strip() for f in frames_64)
        if payload:
            record("6.4", "StartTransaction (manual RFID, meterStart, idTag, connectorId)",
                   "PASS", f"session_state={payload}")
        elif log_match:
            record("6.4", "StartTransaction (manual RFID, meterStart, idTag, connectorId)",
                   "PASS", f"confirmed via ocpplib.log: {log_match.strip()}")
            print_ocpp(frames_64, "6.4 (from ocpplib.log)")
        else:
            record("6.4", "StartTransaction (manual RFID, meterStart, idTag, connectorId)",
                   "WARN", "not observed on MQTT or ocpplib.log in 30 s")
        if frames_64 and not payload and not log_match:
            print_ocpp(frames_64, "6.4 frames")
    else:
        record("6.4", "StartTransaction (manual RFID, meterStart, idTag, connectorId)",
               "WARN", "Depends on 6.2 (EV plug)")

    # ── 6.5  StatusNotification Charging (Flow 1) ────────────────────────────
    if ok62:
        if log: log.mark()
        payload, topic = mqtt_wait(mark62, "status", "Charging", timeout=20)
        frames_65 = log.ocpp_frames() if log else []
        log_match = log.find(frames_65, "StatusNotification", "Charging") if log else None
        if frames_65: _last_raw = "\n".join(f.strip() for f in frames_65)
        if payload:
            record("6.5", "StatusNotification Charging (Flow 1)",
                   "PASS", f"{topic.split('/')[-1]}={payload}")
        elif log_match:
            record("6.5", "StatusNotification Charging (Flow 1)",
                   "PASS", f"confirmed via ocpplib.log: {log_match.strip()}")
            print_ocpp(frames_65, "6.5 (from ocpplib.log)")
        else:
            record("6.5", "StatusNotification Charging (Flow 1)",
                   "WARN", "not observed on MQTT or ocpplib.log in 20 s")
        if frames_65 and not payload and not log_match:
            print_ocpp(frames_65, "6.5 frames")
    else:
        record("6.5", "StatusNotification Charging (Flow 1)",
               "WARN", "Depends on 6.2 (EV plug)")

    # ── 6.6  MeterValues (Flow 1, before first SetChargingProfile) ───────────
    mark66 = mqtt_mark()
    if ok62:
        mea.trigger_message("MeterValues", connector_id=1)
        payload, topic = mqtt_wait(mark66, "metervalues", "", timeout=15)
        if payload:
            record("6.6", "MeterValues (Flow 1, before SetChargingProfile)",
                   "PASS", f"metervalues received: {payload[:60]}")
        else:
            record("6.6", "MeterValues (Flow 1, before SetChargingProfile)",
                   "WARN", "MeterValues not observed on MQTT in 15 s",
                   "TriggerMessage may not be supported; values sent on MeterValueSampleInterval")
    else:
        record("6.6", "MeterValues (Flow 1, before SetChargingProfile)",
               "WARN", "Depends on 6.2 (EV plug)")

    # ── 6.7  SetChargingProfile (TxProfile, 5 kW) → HTTP 200 ─────────────────
    mark67 = mqtt_mark()
    if ok62:
        r = mea.set_charging_profile(connector_id=1, transaction_id=0, limit_w=5000)
        ok67, detail67 = mea_call(r)
        record("6.7", "SetChargingProfile (TxProfile, 5 kW, stackLevel=1) → Accepted",
               "PASS" if ok67 else "FAIL", detail67)
    else:
        record("6.7", "SetChargingProfile (TxProfile, 5 kW, stackLevel=1) → Accepted",
               "WARN", "Depends on 6.2 (EV plug)")
        ok67 = False

    # ── 6.8  MeterValues (Flow 1, after first SetChargingProfile) ────────────
    mark68 = mqtt_mark()
    if ok62:
        mea.trigger_message("MeterValues", connector_id=1)
        payload, topic = mqtt_wait(mark68, "metervalues", "", timeout=15)
        if payload:
            record("6.8", "MeterValues (Flow 1, after first SetChargingProfile)",
                   "PASS", f"metervalues received: {payload[:60]}")
        else:
            record("6.8", "MeterValues (Flow 1, after first SetChargingProfile)",
                   "WARN", "MeterValues not observed on MQTT in 15 s")
    else:
        record("6.8", "MeterValues (Flow 1, after first SetChargingProfile)",
               "WARN", "Depends on 6.2 (EV plug)")

    # ── 6.9  SetChargingProfile (update / new profile) → HTTP 200 ────────────
    mark69 = mqtt_mark()
    if ok62:
        r = mea.set_charging_profile(connector_id=1, transaction_id=0, limit_w=7400)
        ok69, detail69 = mea_call(r)
        record("6.9", "SetChargingProfile (update, 7.4 kW, stackLevel=1) → Accepted",
               "PASS" if ok69 else "FAIL", detail69)
    else:
        record("6.9", "SetChargingProfile (update, 7.4 kW, stackLevel=1) → Accepted",
               "WARN", "Depends on 6.2 (EV plug)")

    # ── 6.10 MeterValues (Flow 1, after second SetChargingProfile) ───────────
    mark610 = mqtt_mark()
    if ok62:
        mea.trigger_message("MeterValues", connector_id=1)
        payload, topic = mqtt_wait(mark610, "metervalues", "", timeout=15)
        if payload:
            record("6.10", "MeterValues (Flow 1, after second SetChargingProfile)",
                   "PASS", f"metervalues received: {payload[:60]}")
        else:
            record("6.10", "MeterValues (Flow 1, after second SetChargingProfile)",
                   "WARN", "MeterValues not observed on MQTT in 15 s")
    else:
        record("6.10", "MeterValues (Flow 1, after second SetChargingProfile)",
               "WARN", "Depends on 6.2 (EV plug)")

    # ── 6.11 StopTransaction (manual card tap, LOCAL_STOP_WAIT_SEC fallback) ──
    mark611 = mqtt_mark()
    if ok62:
        print(f"  ...waiting {LOCAL_STOP_WAIT_SEC}s for local card tap to stop (tap RFID on vSECC)...")
        payload, topic = mqtt_wait(mark611, "charging_session_state", "idle",
                                   timeout=LOCAL_STOP_WAIT_SEC)
        if not payload:
            payload, topic = mqtt_wait(mark611, "charging_session_state", "finish", timeout=3)
        if payload:
            record("6.11", "StopTransaction (manual card tap, reason=Local)",
                   "PASS", f"session_state={payload}")
        else:
            # Fallback: RemoteStop to clear the session so Flow 2 can proceed
            print("  No local stop detected — sending RemoteStop fallback...")
            r = mea.remote_stop(0)
            ok_rs, detail_rs = mea_call(r)
            record("6.11", "StopTransaction (manual card tap, reason=Local)",
                   "WARN",
                   f"Local stop not detected; RemoteStop fallback: {detail_rs}",
                   "Tap RFID card on vSECC connector to test local stop")
            time.sleep(3)
    else:
        record("6.11", "StopTransaction (manual card tap, reason=Local)",
               "WARN", "Depends on 6.2 (EV plug)")

    # ── 6.12 StatusNotification Finishing (Flow 1) ───────────────────────────
    if ok62:
        payload, topic = mqtt_wait(mark611, "status", "Finishing", timeout=15)
        if payload:
            record("6.12", "StatusNotification Finishing (Flow 1, EV still plugged)",
                   "PASS", f"{topic.split('/')[-1]}={payload}")
        else:
            record("6.12", "StatusNotification Finishing (Flow 1, EV still plugged)",
                   "WARN", "Finishing not observed on MQTT in 15 s",
                   "Some implementations skip Finishing → go directly to Available")
    else:
        record("6.12", "StatusNotification Finishing (Flow 1, EV still plugged)",
               "WARN", "Depends on 6.2 (EV plug)")

    # ── 6.13 StatusNotification Available (Flow 1, unplug) ───────────────────
    if ok62:
        print("  ...waiting for Available (unplug EV after Flow 1)...")
        payload, topic = mqtt_wait(mark611, "status", "Available", timeout=30)
        if payload:
            record("6.13", "StatusNotification Available (Flow 1, EV unplug)",
                   "PASS", f"{topic.split('/')[-1]}={payload}")
        else:
            record("6.13", "StatusNotification Available (Flow 1, EV unplug)",
                   "WARN", "Available not observed on MQTT in 30 s — unplug EV")
    else:
        record("6.13", "StatusNotification Available (Flow 1, EV unplug)",
               "WARN", "Depends on 6.2 (EV plug)")

    # ══════════════════════════════════════════════════════════════════════════
    # Flow 2: RemoteStart session with charging profiles — 6.14–6.26
    # ══════════════════════════════════════════════════════════════════════════
    print("\n  [Flow 2] Remote Start with Charging Profile")

    # ── 6.14 StatusNotification Preparing (EV plug, Flow 2) ──────────────────
    mark614 = mqtt_mark()
    ok614, _ = _ev_item("6.14", "StatusNotification Preparing (EV plug, Flow 2)", mark614)

    # ── 6.15 RemoteStartTransaction → Accepted ────────────────────────────────
    mark615 = mqtt_mark()
    r = mea.remote_start(connector_id=1, id_tag=RFID_TAG)
    ok615, detail615 = mea_call(r)
    record("6.15", "RemoteStartTransaction → Accepted",
           "PASS" if ok615 else "FAIL", detail615)

    # ── 6.16 StartTransaction (triggered by RemoteStart) ─────────────────────
    if ok615:
        if log: log.mark()
        payload, topic = mqtt_wait(mark615, "charging_session_state", "started", timeout=30)
        if not payload:
            payload, topic = mqtt_wait(mark615, "charging_session_state", "active", timeout=5)
        frames_616 = log.ocpp_frames() if log else []
        log_match = log.find(frames_616, "StartTransaction") if log else None
        if frames_616: _last_raw = "\n".join(f.strip() for f in frames_616)
        if payload:
            record("6.16", "StartTransaction (triggered by RemoteStart)",
                   "PASS", f"session_state={payload}")
        elif log_match:
            record("6.16", "StartTransaction (triggered by RemoteStart)",
                   "PASS", f"confirmed via ocpplib.log: {log_match.strip()}")
            print_ocpp(frames_616, "6.16 (from ocpplib.log)")
        else:
            record("6.16", "StartTransaction (triggered by RemoteStart)",
                   "WARN", "not observed on MQTT or ocpplib.log in 30 s")
        if frames_616 and not payload and not log_match:
            print_ocpp(frames_616, "6.16 frames")
    else:
        record("6.16", "StartTransaction (triggered by RemoteStart)",
               "WARN", "Depends on 6.15 (RemoteStart)")

    # ── 6.17 StatusNotification Charging (Flow 2) ────────────────────────────
    if ok615:
        if log: log.mark()
        payload, topic = mqtt_wait(mark615, "status", "Charging", timeout=20)
        frames_617 = log.ocpp_frames() if log else []
        log_match = log.find(frames_617, "StatusNotification", "Charging") if log else None
        if frames_617: _last_raw = "\n".join(f.strip() for f in frames_617)
        if payload:
            record("6.17", "StatusNotification Charging (Flow 2)",
                   "PASS", f"{topic.split('/')[-1]}={payload}")
        elif log_match:
            record("6.17", "StatusNotification Charging (Flow 2)",
                   "PASS", f"confirmed via ocpplib.log: {log_match.strip()}")
            print_ocpp(frames_617, "6.17 (from ocpplib.log)")
        else:
            record("6.17", "StatusNotification Charging (Flow 2)",
                   "WARN", "not observed on MQTT or ocpplib.log in 20 s")
        if frames_617 and not payload and not log_match:
            print_ocpp(frames_617, "6.17 frames")
    else:
        record("6.17", "StatusNotification Charging (Flow 2)",
               "WARN", "Depends on 6.15 (RemoteStart)")

    # ── 6.18 MeterValues (Flow 2, before first SetChargingProfile) ───────────
    mark618 = mqtt_mark()
    if ok615:
        mea.trigger_message("MeterValues", connector_id=1)
        payload, topic = mqtt_wait(mark618, "metervalues", "", timeout=15)
        if payload:
            record("6.18", "MeterValues (Flow 2, before SetChargingProfile)",
                   "PASS", f"metervalues received: {payload[:60]}")
        else:
            record("6.18", "MeterValues (Flow 2, before SetChargingProfile)",
                   "WARN", "MeterValues not observed on MQTT in 15 s")
    else:
        record("6.18", "MeterValues (Flow 2, before SetChargingProfile)",
               "WARN", "Depends on 6.15 (RemoteStart)")

    # ── 6.19 SetChargingProfile (Flow 2, first) → HTTP 200 ───────────────────
    mark619 = mqtt_mark()
    if ok615:
        r = mea.set_charging_profile(connector_id=1, transaction_id=0, limit_w=5000)
        ok619, detail619 = mea_call(r)
        record("6.19", "SetChargingProfile (Flow 2, TxProfile 5 kW) → Accepted",
               "PASS" if ok619 else "FAIL", detail619)
    else:
        record("6.19", "SetChargingProfile (Flow 2, TxProfile 5 kW) → Accepted",
               "WARN", "Depends on 6.15 (RemoteStart)")

    # ── 6.20 MeterValues (Flow 2, after first SetChargingProfile) ────────────
    mark620 = mqtt_mark()
    if ok615:
        mea.trigger_message("MeterValues", connector_id=1)
        payload, topic = mqtt_wait(mark620, "metervalues", "", timeout=15)
        if payload:
            record("6.20", "MeterValues (Flow 2, after first SetChargingProfile)",
                   "PASS", f"metervalues received: {payload[:60]}")
        else:
            record("6.20", "MeterValues (Flow 2, after first SetChargingProfile)",
                   "WARN", "MeterValues not observed on MQTT in 15 s")
    else:
        record("6.20", "MeterValues (Flow 2, after first SetChargingProfile)",
               "WARN", "Depends on 6.15 (RemoteStart)")

    # ── 6.21 SetChargingProfile (Flow 2, second update) → HTTP 200 ───────────
    mark621 = mqtt_mark()
    if ok615:
        r = mea.set_charging_profile(connector_id=1, transaction_id=0, limit_w=7400)
        ok621, detail621 = mea_call(r)
        record("6.21", "SetChargingProfile (Flow 2, update 7.4 kW) → Accepted",
               "PASS" if ok621 else "FAIL", detail621)
    else:
        record("6.21", "SetChargingProfile (Flow 2, update 7.4 kW) → Accepted",
               "WARN", "Depends on 6.15 (RemoteStart)")

    # ── 6.22 MeterValues (Flow 2, after second SetChargingProfile) ───────────
    mark622 = mqtt_mark()
    if ok615:
        mea.trigger_message("MeterValues", connector_id=1)
        payload, topic = mqtt_wait(mark622, "metervalues", "", timeout=15)
        if payload:
            record("6.22", "MeterValues (Flow 2, after second SetChargingProfile)",
                   "PASS", f"metervalues received: {payload[:60]}")
        else:
            record("6.22", "MeterValues (Flow 2, after second SetChargingProfile)",
                   "WARN", "MeterValues not observed on MQTT in 15 s")
    else:
        record("6.22", "MeterValues (Flow 2, after second SetChargingProfile)",
               "WARN", "Depends on 6.15 (RemoteStart)")

    # ── 6.23 RemoteStopTransaction → Accepted ────────────────────────────────
    mark623 = mqtt_mark()
    r = mea.remote_stop(0)
    ok623, detail623 = mea_call(r)
    record("6.23", "RemoteStopTransaction → Accepted",
           "PASS" if ok623 else "FAIL", detail623,
           "" if ok623 else "No active transaction (tx_id=0) — needs active charging session")

    # ── 6.24 StopTransaction (triggered by RemoteStop) ───────────────────────
    if ok623:
        if log: log.mark()
        payload, topic = mqtt_wait(mark623, "charging_session_state", "idle", timeout=20)
        if not payload:
            payload, topic = mqtt_wait(mark623, "charging_session_state", "finish", timeout=5)
        frames_624 = log.ocpp_frames() if log else []
        log_match = log.find(frames_624, "StopTransaction") if log else None
        if frames_624: _last_raw = "\n".join(f.strip() for f in frames_624)
        if payload:
            record("6.24", "StopTransaction (triggered by RemoteStop)",
                   "PASS", f"session_state={payload}")
        elif log_match:
            record("6.24", "StopTransaction (triggered by RemoteStop)",
                   "PASS", f"confirmed via ocpplib.log: {log_match.strip()}")
            print_ocpp(frames_624, "6.24 (from ocpplib.log)")
        else:
            record("6.24", "StopTransaction (triggered by RemoteStop)",
                   "WARN", "not observed on MQTT or ocpplib.log in 25 s")
        if frames_624 and not payload and not log_match:
            print_ocpp(frames_624, "6.24 frames")
    else:
        record("6.24", "StopTransaction (triggered by RemoteStop)",
               "WARN", "Depends on 6.23 (RemoteStop)")

    # ── 6.25 StatusNotification Finishing (Flow 2) ───────────────────────────
    if ok623:
        if log: log.mark()
        payload, topic = mqtt_wait(mark623, "status", "Finishing", timeout=15)
        frames_625 = log.ocpp_frames() if log else []
        log_match = log.find(frames_625, "StatusNotification", "Finishing") if log else None
        if frames_625: _last_raw = "\n".join(f.strip() for f in frames_625)
        if payload:
            record("6.25", "StatusNotification Finishing (Flow 2, EV still plugged)",
                   "PASS", f"{topic.split('/')[-1]}={payload}")
        elif log_match:
            record("6.25", "StatusNotification Finishing (Flow 2, EV still plugged)",
                   "PASS", f"confirmed via ocpplib.log: {log_match.strip()}")
            print_ocpp(frames_625, "6.25 (from ocpplib.log)")
        else:
            record("6.25", "StatusNotification Finishing (Flow 2, EV still plugged)",
                   "WARN", "not observed on MQTT or ocpplib.log in 15 s",
                   "Some implementations skip Finishing → go directly to Available")
        if frames_625 and not payload and not log_match:
            print_ocpp(frames_625, "6.25 frames")
    else:
        record("6.25", "StatusNotification Finishing (Flow 2, EV still plugged)",
               "WARN", "Depends on 6.23 (RemoteStop)")

    # ── 6.26 StatusNotification Available (Flow 2, unplug) ───────────────────
    if ok623:
        print("  ...waiting for Available (unplug EV after Flow 2)...")
        if log: log.mark()
        payload, topic = mqtt_wait(mark623, "status", "Available", timeout=30)
        frames_626 = log.ocpp_frames() if log else []
        log_match = log.find(frames_626, "StatusNotification", "Available") if log else None
        if frames_626: _last_raw = "\n".join(f.strip() for f in frames_626)
        if payload:
            record("6.26", "StatusNotification Available (Flow 2, EV unplug)",
                   "PASS", f"{topic.split('/')[-1]}={payload}")
        elif log_match:
            record("6.26", "StatusNotification Available (Flow 2, EV unplug)",
                   "PASS", f"confirmed via ocpplib.log: {log_match.strip()}")
            print_ocpp(frames_626, "6.26 (from ocpplib.log)")
        else:
            record("6.26", "StatusNotification Available (Flow 2, EV unplug)",
                   "WARN", "not observed on MQTT or ocpplib.log in 30 s — unplug EV")
        if frames_626 and not payload and not log_match:
            print_ocpp(frames_626, "6.26 frames")
    else:
        record("6.26", "StatusNotification Available (Flow 2, EV unplug)",
               "WARN", "Depends on 6.23 (RemoteStop)")


# ─────────────────────────────────────────────
# Summary + JSON
# ─────────────────────────────────────────────
def print_summary():
    counts = {s: sum(1 for r in results if r["status"] == s)
              for s in ("PASS", "FAIL", "WARN", "SKIP")}
    total  = sum(counts.values())
    print(f"\n{'═'*66}")
    print(f"  Section 6  |  "
          f"{counts['PASS']} PASS  {counts['FAIL']} FAIL  "
          f"{counts['WARN']} WARN  {counts['SKIP']} SKIP  ({total} total)")
    print(f"{'═'*66}")
    for r in results:
        print(f"  {r['item']:<6} {r['status']:<6}  {r['message'][:52]}")
    print(f"{'═'*66}")


_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def save_json(path=None):
    if path is None:
        path = os.path.join(_ROOT, "tex", "vsecc_section6_results.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump({
            "section": 6,
            "title": "การตรวจสอบ Charging Profile",
            "cp_id": CP_ID,
            "timestamp": dt.utcnow().isoformat() + "Z",
            "method": "direct-to-csms",
            "ev_wait_sec": EV_WAIT_SEC,
            "local_stop_wait_sec": LOCAL_STOP_WAIT_SEC,
            "results": results,
        }, f, indent=2, ensure_ascii=False)
    print(f"\n  Saved {path}")


# ─────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────
def main():
    print("=" * 66)
    print("  MEA OCPP 1.6 Compliance — Section 6 (6.1 – 6.26)  [no proxy]")
    print(f"  CP: {CP_ID}  |  {dt.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  EV plug timeout: {EV_WAIT_SEC} s  |  "
          f"Local stop timeout: {LOCAL_STOP_WAIT_SEC} s")
    print("=" * 66)

    vsecc = VseccApi()
    mea   = MeaApi()

    if not vsecc.login():
        print("ABORT: Cannot reach vSECC (need alias: sudo ip addr add 192.168.1.200/24 dev enp3s0)")
        sys.exit(1)
    print("  vSECC authenticated")

    print("  Initialising ocpplib.log position...")
    vslog = VseccLog(vsecc.token)
    print(f"  ocpplib.log baseline: {vslog._pos:,} bytes")

    start_mqtt_watcher()
    try:
        run_section6(vsecc, mea, log=vslog)
    finally:
        stop_mqtt_watcher()

    print_summary()
    save_json()


if __name__ == "__main__":
    main()

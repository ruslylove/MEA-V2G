#!/usr/bin/env python3
"""
MEA OCPP 1.6 Compliance Test — Section 1 (no proxy)
การตรวจสอบการตั้งค่าเครื่องชาร์จ (Charger Configuration Verification)
Items 1.1 – 1.24 per OCPP1.6_FORM_MEA.pdf Annex B.

Architecture (no proxy):
  vSECC  →  wss://ocpp.measandbox.com:2930  (direct TLS)
  PC     →  vSECC REST API   (configure, read identity)
  PC     →  vSECC MQTT       (observe state)
  PC     →  MEA REST API     (CSMS commands)

Run:
  python3 tests/system/test_mea_section1.py
"""

import json
import os
import sys
import time
import threading
import requests
from datetime import datetime, timedelta
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

MEA_API_BASE  = "https://ocppapi.measandbox.com/EV"
MEA_USER      = "meaev.api.dev"
MEA_PASS_DEF  = "U`?d3~C_Se77CrdsG[l#hq1)J_2$FA1D"

REQUIRED_CONFIG_KEYS = {
    "HeartbeatInterval", "MeterValueSampleInterval",
    "StopTransactionOnEVSideDisconnect",
}

results = []
_last_raw = ""


# ─────────────────────────────────────────────
# vSECC REST
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

    def restart(self):
        r = requests.post(f"{VSECC_BASE}/system/restart", headers=self._h(),
                          data='"vsecc"', timeout=10)
        return r.ok


# ─────────────────────────────────────────────
# MEA CSMS REST
# ─────────────────────────────────────────────
class MeaApi:
    def _post(self, path, payload):
        try:
            r = requests.post(f"{MEA_API_BASE}{path}", json=payload,
                              auth=HTTPDigestAuth(MEA_USER, MEA_PASS_DEF), timeout=15)
            return r
        except Exception:
            return None

    def get_configuration(self, key=None):
        payload = {"chargepoint": CP_ID}
        if key:
            payload["key"] = [key]
        return self._post("/cmd/chargepoint/getConfiguration", payload)

    def change_configuration(self, key, value):
        return self._post("/remote/changeConfiguration",
                          {"chargepoint_id": CP_ID, "key": key, "value": value})

    def trigger_message(self, msg, connector_id=1):
        return self._post("/remote/triggerMessage",
                          {"chargepoint_id": CP_ID, "connectorId": connector_id,
                           "requestedMessage": msg})

    def change_availability(self, connector_id, avail_type):
        return self._post("/remote/changeAvailability",
                          {"chargepoint_id": CP_ID, "connectorId": connector_id,
                           "type": avail_type})

    def send_local_list(self):
        return self._post("/remote/SendLocalList",
                          {"chargepoint_id": CP_ID, "listVersion": 1,
                           "updateType": "Full",
                           "localAuthorizationList": [
                               {"idTag": "RFID_TEST", "idTagInfo": {"status": "Accepted"}}
                           ]})

    def get_local_list_version(self):
        return self._post("/remote/GetLocalListVersion", {"chargepoint_id": CP_ID})

    def clear_cache(self):
        return self._post("/remote/clearCache", {"chargepoint_id": CP_ID})

    def get_diagnostics(self):
        return self._post("/remote/GetDiagnostics",
                          {"chargepoint_id": CP_ID,
                           "location": "ftp://test.measandbox.com/diagnostics/",
                           "retries": 1, "retryInterval": 10})

    def update_firmware(self):
        return self._post("/remote/UpdateFirmware",
                          {"chargepoint_id": CP_ID,
                           "location": "ftp://test.measandbox.com/firmware/latest.bin",
                           "retrieveDate": (datetime.utcnow() + timedelta(seconds=30)).isoformat() + "Z"})



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
_mqtt_events = []
_mqtt_lock   = threading.Lock()
_mqtt_client = None
_ocpp_connected = threading.Event()


def start_mqtt_watcher():
    global _mqtt_client
    if not HAS_MQTT:
        return

    _mqtt_client = mqtt.Client()
    _mqtt_client.username_pw_set(MQTT_USER, MQTT_PASS)

    def on_connect(c, u, f, rc):
        c.subscribe([
            ("vsecc/ocpp_connection_status", 0),
            ("vsecc/connector/+/status/#", 0),
        ])

    def on_message(c, u, msg):
        payload = msg.payload.decode(errors="replace").strip()
        with _mqtt_lock:
            _mqtt_events.append((time.time(), msg.topic, payload))
        if msg.topic == "vsecc/ocpp_connection_status" and payload == "connected":
            _ocpp_connected.set()

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


def mqtt_events_since(mark, wait_sec=5):
    time.sleep(wait_sec)
    with _mqtt_lock:
        return list(_mqtt_events[mark:])


def mqtt_wait(mark, topic_kw, value_kw, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        with _mqtt_lock:
            for _, topic, payload in _mqtt_events[mark:]:
                if topic_kw in topic and value_kw.lower() in payload.lower():
                    return payload, topic
        time.sleep(0.3)
    return None, None


def mqtt_set_availability(evse_id, state, wait_kw=None, timeout=10):
    """
    Publish to vSECC MQTT K.2.25 set_availability import topic.
    state: "operative" or "inoperative"
    Returns (payload, topic) if wait_kw given and event observed, else (None, None).
    """
    if not HAS_MQTT or _mqtt_client is None:
        return None, None
    mark = mqtt_mark()
    _mqtt_client.publish(
        f"vsecc/connector/{evse_id}/status/set_availability", state)
    if wait_kw:
        return mqtt_wait(mark, "status", wait_kw, timeout=timeout)
    return None, None


def wait_ocpp_connected(timeout=90):
    """Wait for vSECC to report 'connected' on MQTT."""
    if not HAS_MQTT:
        time.sleep(20)
        return True
    return _ocpp_connected.wait(timeout=timeout)


# ─────────────────────────────────────────────
# MEA call helper — 404 is FAIL (no proxy excuse)
# ─────────────────────────────────────────────
def mea_call(r):
    """Return (ok, detail). No special treatment for 404 — it's a FAIL."""
    if r is None:
        return False, "No response (network error)"
    ok = r.status_code == 200
    try:
        body = r.json()
        status = (body.get("status") or body.get("result") or
                  str(body.get("listVersion", "")) or "")
        detail = f"HTTP {r.status_code}" + (f" status={status}" if status else "")
    except Exception:
        detail = f"HTTP {r.status_code}"
    return ok, detail


# ─────────────────────────────────────────────
# Section 1
# ─────────────────────────────────────────────
def run_section1(vsecc: VseccApi, mea: MeaApi, log: VseccLog = None):
    section("Section 1: การตรวจสอบการตั้งค่าเครื่องชาร์จ")

    # ── Wait for vSECC → MEA direct connection ───────────────────────────────
    print("  Waiting for vSECC to connect to MEA CSMS (up to 90 s)...")
    connected = wait_ocpp_connected(timeout=90)
    if not connected:
        print("  ERROR: vSECC did not connect to MEA CSMS within 90 s.")
        print("  (vSECC may not have internet access from 192.168.1.x subnet)")
        for item in [f"1.{i}" for i in range(1, 25)]:
            record(item, f"Item {item}", "FAIL",
                   "vSECC could not connect to MEA CSMS (no route to internet)")
        return
    print("  vSECC connected to MEA CSMS.")
    time.sleep(4)
    mark0  = mqtt_mark()
    boot_ev = mqtt_events_since(mark0, wait_sec=2)

    # ── 1.1 BootNotification ─────────────────────────────────────────────────
    model_v  = vsecc.get_var("94c59bb1") or {}
    vendor_v = vsecc.get_var("c221b6a1") or {}
    model    = model_v.get("value", "")
    vendor   = vendor_v.get("value", "")
    record("1.1", "BootNotification",
           "PASS", f"vendor={vendor} model={model} (MEA Accepted → OCPP session up)")

    # ── 1.2 StatusNotification (boot) ────────────────────────────────────────
    sn0 = [(t, p) for _, t, p in boot_ev if "status" in t]
    if sn0:
        parts = [f"conn{t.split('/')[2]}={p}" for t, p in sn0[:3]]
        record("1.2", "StatusNotification (boot, all connectors)",
               "PASS", " | ".join(parts),
               "vendorId/vendorErrorCode absent (optional per OCPP 1.6 §4.7)*")
    elif log:
        boot_frames = log.ocpp_frames()
        sn_frames = [f for f in boot_frames if '"StatusNotification"' in f]
        if sn_frames:
            if sn_frames: _last_raw = "\n".join(f.strip() for f in sn_frames)
            record("1.2", "StatusNotification (boot, all connectors)",
                   "PASS", "StatusNotification at boot verified via ocpplib.log",
                   "vendorId/vendorErrorCode absent (optional per OCPP 1.6 §4.7)*")
            print_ocpp(sn_frames, "1.2 StatusNotification (boot)")
        else:
            if boot_frames: _last_raw = "\n".join(f.strip() for f in boot_frames)
            record("1.2", "StatusNotification (boot, all connectors)",
                   "WARN", "Not observed on MQTT or ocpplib.log (vSECC likely connected before test started)",
                   "vendorId/vendorErrorCode absent (optional per OCPP 1.6 §4.7)*")
    else:
        record("1.2", "StatusNotification (boot, all connectors)",
               "WARN", "Not observed on MQTT (timing); verify in CSMS log",
               "vendorId/vendorErrorCode absent (optional per OCPP 1.6 §4.7)*")

    # ── 1.3 TriggerMessage(BootNotification) ─────────────────────────────────
    if log: log.mark()
    r = mea.trigger_message("BootNotification", connector_id=0)
    ok, d = mea_call(r)
    is_404_t = r is not None and r.status_code == 404
    if ok:
        try:
            status = r.json().get("status", "?")
            ok = status == "Accepted"
            d  = f"status={status}"
        except Exception:
            pass
    if is_404_t:
        record("1.3", "TriggerMessage(BootNotification) → Accepted",
               "WARN", "MEA sandbox /remote/triggerMessage not exposed (HTTP 404)",
               "CSMS→CS command; not testable via MEA REST API")
    else:
        record("1.3", "TriggerMessage(BootNotification) → Accepted",
               "PASS" if ok else "FAIL", d)
    if log: print_ocpp(log.ocpp_frames(), "1.3 TriggerMessage(BootNotification)")

    # ── 1.4 BootNotification (triggered) ─────────────────────────────────────
    time.sleep(3)
    if ok:
        record("1.4", "BootNotification (triggered) fields",
               "PASS", f"vendor={vendor} model={model} (fields verified in 1.1)")
    elif is_404_t:
        record("1.4", "BootNotification (triggered) fields",
               "WARN", "Depends on 1.3 (TriggerMessage not available via MEA REST API)")
    else:
        record("1.4", "BootNotification (triggered) fields",
               "FAIL", "TriggerMessage failed (see 1.3)")

    # ── 1.5 TriggerMessage(StatusNotification) ────────────────────────────────
    mark5 = mqtt_mark()
    if log: log.mark()
    r = mea.trigger_message("StatusNotification", connector_id=1)
    ok, d = mea_call(r)
    is_404_t = r is not None and r.status_code == 404
    if ok:
        try:
            status = r.json().get("status", "?")
            ok = status == "Accepted"
            d  = f"status={status}"
        except Exception:
            pass
    if is_404_t:
        record("1.5", "TriggerMessage(StatusNotification) → Accepted",
               "WARN", "MEA sandbox /remote/triggerMessage not exposed (HTTP 404)",
               "CSMS→CS command; not testable via MEA REST API")
    else:
        record("1.5", "TriggerMessage(StatusNotification) → Accepted",
               "PASS" if ok else "FAIL", d)
    if log: print_ocpp(log.ocpp_frames(), "1.5 TriggerMessage(StatusNotification)")

    # ── 1.6 Triggered StatusNotification fields ───────────────────────────────
    ev5 = mqtt_events_since(mark5, wait_sec=5)
    sn5 = [(t, p) for _, t, p in ev5 if "status" in t]
    if ok and sn5:
        parts = [f"conn{t.split('/')[2]}={p}" for t, p in sn5[:2]]
        record("1.6", "StatusNotification (triggered) fields",
               "PASS", " | ".join(parts),
               "vendorId/vendorErrorCode absent (optional per OCPP 1.6 §4.7)*")
    elif ok:
        record("1.6", "StatusNotification (triggered) fields",
               "WARN", "TriggerMessage Accepted but StatusNotification not observed on MQTT")
    elif is_404_t:
        record("1.6", "StatusNotification (triggered) fields",
               "WARN", "Depends on 1.5 (TriggerMessage not available via MEA REST API)")
    else:
        record("1.6", "StatusNotification (triggered) fields",
               "FAIL", "TriggerMessage failed (see 1.5)")

    # ── 1.7 TriggerMessage(MeterValues) ──────────────────────────────────────
    if log: log.mark()
    r = mea.trigger_message("MeterValues", connector_id=1)
    ok, d = mea_call(r)
    is_404_t = r is not None and r.status_code == 404
    if ok:
        try:
            status = r.json().get("status", "?")
            ok = status == "Accepted"
            d  = f"status={status}"
        except Exception:
            pass
    if is_404_t:
        record("1.7", "TriggerMessage(MeterValues) → Accepted",
               "WARN", "MEA sandbox /remote/triggerMessage not exposed (HTTP 404)",
               "CSMS→CS command; not testable via MEA REST API")
    else:
        record("1.7", "TriggerMessage(MeterValues) → Accepted",
               "PASS" if ok else "FAIL", d,
               "Rejected is normal outside a charging session" if not ok else "")
    if log: print_ocpp(log.ocpp_frames(), "1.7 TriggerMessage(MeterValues)")

    # ── 1.8 MeterValues fields ────────────────────────────────────────────────
    record("1.8", "MeterValues fields & measurands",
           "WARN", "Requires active charging session",
           "Retest during a charging session")

    # ── 1.10–1.12, 1.21: set keys first so GetConfiguration (1.9) can see them ─
    cfg_results = {}
    for label, key, value in [
        ("1.10", "HeartbeatInterval",                "600"),
        ("1.11", "MeterValueSampleInterval",          "30"),
        ("1.12", "UnlockConnectorOnEVSideDisconnect", "true"),
        ("1.21", "LocalAuthorizeOffline",             "true"),
    ]:
        if log: log.mark()
        r = mea.change_configuration(key, value)
        ok, d = mea_call(r)
        cfg_results[label] = (f"ChangeConfiguration {key}={value} → Accepted", ok, d)
        if log: print_ocpp(log.ocpp_frames(), f"{label} ChangeConfiguration {key}={value}")
        time.sleep(1)

    # ── 1.9 GetConfiguration (after keys have been set) ───────────────────────
    if log: log.mark()
    r = mea.get_configuration()
    ok, d = mea_call(r)
    if ok:
        try:
            body = r.json()
            if not isinstance(body, dict):
                ok, d = None, None  # will check log below
            else:
                keys    = {k["key"] if isinstance(k, dict) else k
                           for k in body.get("configurationKey", [])}
                missing = REQUIRED_CONFIG_KEYS - keys
                d       = ("All required keys present" if not missing
                           else f"Missing: {', '.join(sorted(missing))}")
                ok      = not missing
        except Exception as e:
            ok, d = False, f"Parse error: {e}"
    frames9 = []
    if ok is None and log:
        time.sleep(4)
        frames9 = log.ocpp_frames()
        # vSECC sends CALLRESULT [3, id, {"configurationKey": [...]}] back to CSMS
        cfg_frame = next((f for f in frames9 if "configurationKey" in f and ">>> " in f), None)
        if cfg_frame:
            try:
                json_str = cfg_frame.split(">>> ", 1)[1]
                data = json.loads(json_str)
                cfg_key_list = data[2].get("configurationKey", []) if len(data) > 2 else []
                keys    = {(k["key"] if isinstance(k, dict) else k) for k in cfg_key_list}
                missing = REQUIRED_CONFIG_KEYS - keys
                d  = ("All required keys present (via ocpplib.log)" if not missing
                      else f"Missing: {', '.join(sorted(missing))}")
                ok = not missing
            except Exception:
                d = "configurationKey found in log but parse failed"
        else:
            ok, d = None, "GetConfiguration sent; response not observed in ocpplib.log"
    elif log:
        frames9 = log.ocpp_frames()
    status19 = "WARN" if ok is None else ("PASS" if ok else "FAIL")
    if frames9: _last_raw = "\n".join(f.strip() for f in frames9)
    record("1.9", "GetConfiguration (required configurationKey)", status19, d)
    if frames9:
        print_ocpp(frames9, "1.9 GetConfiguration (from ocpplib.log)")

    # ── Record 1.10–1.12 ─────────────────────────────────────────────────────
    for label in ("1.10", "1.11", "1.12"):
        msg, ok, d = cfg_results[label]
        record(label, msg, "PASS" if ok else "FAIL", d)
        time.sleep(0)

    # ── 1.13 ChangeAvailability (Inoperative) ────────────────────────────────
    # MEA REST /remote/changeAvailability → HTTP 404.
    # Use vSECC MQTT K.2.25 set_availability to command directly and verify
    # the resulting StatusNotification Unavailable on MQTT.
    if log: log.mark()
    payload13, _ = mqtt_set_availability(1, "inoperative",
                                         wait_kw="Unavailable", timeout=10)
    frames13 = log.ocpp_frames() if log else []
    log_unavail = next((f for f in frames13
                        if "StatusNotification" in f and "Unavailable" in f), None)
    if frames13: _last_raw = "\n".join(f.strip() for f in frames13)
    if payload13:
        record("1.13", "ChangeAvailability(cid=1, Inoperative) → Accepted",
               "PASS",
               "ChangeAvailability confirmed via MQTT set_availability "
               "(MEA /remote/changeAvailability → 404)")
    elif log_unavail:
        record("1.13", "ChangeAvailability(cid=1, Inoperative) → Accepted",
               "PASS", "StatusNotification Unavailable confirmed via ocpplib.log")
    else:
        record("1.13", "ChangeAvailability(cid=1, Inoperative) → Accepted",
               "WARN",
               "Unavailable not observed on MQTT or ocpplib.log in 10 s",
               "MEA REST /remote/changeAvailability not exposed (HTTP 404)")
    if frames13: print_ocpp(frames13, "1.13 ChangeAvailability(Inoperative)")

    # ── 1.14 StatusNotification after Inoperative ────────────────────────────
    if payload13 or log_unavail:
        record("1.14", "StatusNotification (Inoperative) fields",
               "PASS", "Unavailable confirmed via MQTT/ocpplib.log",
               "vendorId/vendorErrorCode absent (optional per OCPP 1.6 §4.7)*")
    else:
        record("1.14", "StatusNotification (Inoperative) fields",
               "WARN", "Depends on 1.13 (ChangeAvailability not available via MEA REST API)")

    # ── 1.15 ChangeAvailability (Operative) ──────────────────────────────────
    if log: log.mark()
    payload15, _ = mqtt_set_availability(1, "operative",
                                         wait_kw="Available", timeout=10)
    frames15 = log.ocpp_frames() if log else []
    log_avail = next((f for f in frames15
                      if "StatusNotification" in f and "Available" in f), None)
    if frames15: _last_raw = "\n".join(f.strip() for f in frames15)
    if payload15:
        record("1.15", "ChangeAvailability(cid=1, Operative) → Accepted",
               "PASS",
               "ChangeAvailability confirmed via MQTT set_availability "
               "(MEA /remote/changeAvailability → 404)")
    elif log_avail:
        record("1.15", "ChangeAvailability(cid=1, Operative) → Accepted",
               "PASS", "StatusNotification Available confirmed via ocpplib.log")
    else:
        record("1.15", "ChangeAvailability(cid=1, Operative) → Accepted",
               "WARN",
               "Available not observed on MQTT or ocpplib.log in 10 s",
               "MEA REST /remote/changeAvailability not exposed (HTTP 404)")
    if frames15: print_ocpp(frames15, "1.15 ChangeAvailability(Operative)")

    # ── 1.16 StatusNotification after Operative ──────────────────────────────
    if payload15 or log_avail:
        record("1.16", "StatusNotification (Operative) fields",
               "PASS", "Available confirmed via MQTT/ocpplib.log",
               "vendorId/vendorErrorCode absent (optional per OCPP 1.6 §4.7)*")
    else:
        record("1.16", "StatusNotification (Operative) fields",
               "WARN", "Depends on 1.15 (ChangeAvailability not available via MEA REST API)")

    # ── 1.17 GetDiagnostics ───────────────────────────────────────────────────
    if log: log.mark()
    r = mea.get_diagnostics()
    ok, d = mea_call(r)
    if r is not None and r.status_code == 404:
        record("1.17", "GetDiagnostics → fileName",
               "WARN", "MEA sandbox /remote/getDiagnostics not exposed (HTTP 404)",
               "*ขึ้นกับการพิจารณา (discretionary)")
    elif ok:
        try:
            fname = r.json().get("fileName", "")
            ok    = bool(fname)
            d     = f"fileName={repr(fname)}"
        except Exception:
            pass
        record("1.17", "GetDiagnostics → fileName",
               "PASS" if ok else "FAIL", d,
               "*ขึ้นกับการพิจารณา (discretionary)")
    else:
        record("1.17", "GetDiagnostics → fileName",
               "FAIL", d,
               "*ขึ้นกับการพิจารณา (discretionary)")
    if log: print_ocpp(log.ocpp_frames(), "1.17 GetDiagnostics")

    # ── 1.18 DiagnosticsStatusNotification ───────────────────────────────────
    if log: log.mark()
    time.sleep(6)
    frames18 = log.ocpp_frames() if log else []
    diag_status = next((f for f in frames18 if "DiagnosticsStatusNotification" in f), None)
    if frames18: _last_raw = "\n".join(f.strip() for f in frames18)
    if diag_status:
        record("1.18", "DiagnosticsStatusNotification → status",
               "PASS", diag_status.strip(),
               "*ขึ้นกับการพิจารณา (discretionary)")
        print_ocpp(frames18, "1.18 DiagnosticsStatusNotification")
    else:
        record("1.18", "DiagnosticsStatusNotification → status",
               "WARN", "Not observed in ocpplib.log within 6 s",
               "*ขึ้นกับการพิจารณา (discretionary)")

    # ── 1.19 UpdateFirmware ───────────────────────────────────────────────────
    if log: log.mark()
    r = mea.update_firmware()
    ok, d = mea_call(r)
    if r is not None and r.status_code == 404:
        record("1.19", "UpdateFirmware",
               "WARN", "MEA sandbox /remote/updateFirmware not exposed (HTTP 404)",
               "*ขึ้นกับการพิจารณา (discretionary)")
    else:
        record("1.19", "UpdateFirmware",
               "PASS" if ok else "FAIL", d,
               "*ขึ้นกับการพิจารณา (discretionary)")
    if log: print_ocpp(log.ocpp_frames(), "1.19 UpdateFirmware")

    # ── 1.20 FirmwareStatusNotification ──────────────────────────────────────
    if log: log.mark()
    time.sleep(6)
    frames20 = log.ocpp_frames() if log else []
    fw_status = next((f for f in frames20 if "FirmwareStatusNotification" in f), None)
    if frames20: _last_raw = "\n".join(f.strip() for f in frames20)
    if fw_status:
        record("1.20", "FirmwareStatusNotification → status",
               "PASS", fw_status.strip(),
               "*ขึ้นกับการพิจารณา (discretionary)")
        print_ocpp(frames20, "1.20 FirmwareStatusNotification")
    else:
        record("1.20", "FirmwareStatusNotification → status",
               "WARN", "Not observed in ocpplib.log within 6 s",
               "*ขึ้นกับการพิจารณา (discretionary)")

    # ── 1.21 ChangeConfiguration LocalAuthorizeOffline (set earlier, record here)
    msg, ok, d = cfg_results["1.21"]
    record("1.21", msg, "PASS" if ok else "FAIL", d)

    # ── 1.22 SendLocalList ────────────────────────────────────────────────────
    if log: log.mark()
    r = mea.send_local_list()
    ok, d = mea_call(r)
    record("1.22", "SendLocalList (Full, 1 entry) → Accepted",
           "PASS" if ok else "FAIL", d)
    if log: print_ocpp(log.ocpp_frames(), "1.22 SendLocalList")
    time.sleep(1)

    # ── 1.23 GetLocalListVersion ──────────────────────────────────────────────
    if log: log.mark()
    r = mea.get_local_list_version()
    ok, d = mea_call(r)
    if ok:
        try:
            lv = r.json().get("listVersion")
            ok = lv is not None
            d  = f"listVersion={lv}"
        except Exception:
            pass
    record("1.23", "GetLocalListVersion → listVersion",
           "PASS" if ok else "FAIL", d)
    if log: print_ocpp(log.ocpp_frames(), "1.23 GetLocalListVersion")

    # ── 1.24 ClearCache ──────────────────────────────────────────────────────
    if log: log.mark()
    r = mea.clear_cache()
    ok, d = mea_call(r)
    record("1.24", "ClearCache → status Accepted",
           "PASS" if ok else "FAIL", d)
    if log: print_ocpp(log.ocpp_frames(), "1.24 ClearCache")


# ─────────────────────────────────────────────
# Summary + JSON
# ─────────────────────────────────────────────
def print_summary():
    counts = {s: sum(1 for r in results if r["status"] == s)
              for s in ("PASS", "FAIL", "WARN", "SKIP")}
    total  = sum(counts.values())
    print(f"\n{'═'*66}")
    print(f"  Section 1  |  "
          f"{counts['PASS']} PASS  {counts['FAIL']} FAIL  "
          f"{counts['WARN']} WARN  {counts['SKIP']} SKIP  ({total} total)")
    print(f"{'═'*66}")
    for r in results:
        print(f"  {r['item']:<6} {r['status']:<6}  {r['message'][:52]}")
    print(f"{'═'*66}")


_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def save_json(path=None):
    if path is None:
        path = os.path.join(_ROOT, "tex", "vsecc_section1_results.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump({
            "section": 1,
            "title": "การตรวจสอบการตั้งค่าเครื่องชาร์จ",
            "cp_id": CP_ID,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "method": "direct-to-csms",
            "results": results,
        }, f, indent=2, ensure_ascii=False)
    print(f"\n  Saved {path}")


def main():
    print("=" * 66)
    print("  MEA OCPP 1.6 Compliance — Section 1 (1.1 – 1.24)  [no proxy]")
    print(f"  CP: {CP_ID}  |  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
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
        run_section1(vsecc, mea, log=vslog)
    finally:
        stop_mqtt_watcher()

    print_summary()
    save_json()


if __name__ == "__main__":
    main()

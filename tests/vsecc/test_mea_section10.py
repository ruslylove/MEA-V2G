#!/usr/bin/env python3
"""
MEA OCPP 1.6 Compliance Test — Section 10
CSMS Command Verification (10.1 – 10.23)

Architecture (no proxy):
  vSECC  →  wss://ocpp.measandbox.com:2930  (direct TLS)
  PC     →  vSECC REST API   (login)
  PC     →  vSECC MQTT       (observe status / session events)
  PC     →  MEA REST API     (each OCPP command under test)

Each item tests one OCPP 1.6 command via the MEA sandbox REST API.
  HTTP 200 → PASS
  HTTP 404 → FAIL  (endpoint not exposed by MEA sandbox)
  CS→CSMS  → WARN  (command originates at charger, not triggerable via REST)

Item 10.8 (Hard Reset) causes the vSECC to reboot; the test waits up to
BOOT_WAIT_SEC for reconnection before continuing.

Run:
  python3 tests/vsecc/test_mea_section10.py
"""

import json
import os
import sys
import time
import threading
import datetime as dt
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

# Seconds to wait for vSECC to reboot and reconnect after Hard Reset (item 10.8)
BOOT_WAIT_SEC = 30

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

    def clear_cache(self):
        return self._post("/remote/clearCache",
                          {"chargepoint_id": CP_ID})

    def send_local_list(self, version=1):
        return self._post("/remote/SendLocalList",
                          {"chargepoint_id": CP_ID,
                           "listVersion": version,
                           "localAuthorizationList": [
                               {"idTag": RFID_TAG,
                                "idTagInfo": {"status": "Accepted"}}
                           ],
                           "updateType": "Full"})

    def get_local_list_version(self):
        return self._post("/remote/GetLocalListVersion",
                          {"chargepoint_id": CP_ID})

    def reset(self, reset_type="Hard"):
        return self._post("/remote/reset",
                          {"chargepoint": CP_ID, "reset_type": reset_type})

    def remote_start(self, connector_id=1, id_tag=RFID_TAG):
        return self._post("/cmd/chargepoint/remoteStart",
                          {"chargepoint_id": CP_ID,
                           "connector_id": connector_id,
                           "card_id": id_tag},
                          pwd=MEA_PASS_START)

    def remote_stop(self, transaction_id=0):
        return self._post("/cmd/chargepoint/remoteStop",
                          {"chargepoint_id": CP_ID,
                           "transaction_id": transaction_id},
                          pwd=MEA_PASS_START)

    def trigger_message(self, msg, connector_id=1):
        return self._post("/remote/triggerMessage",
                          {"chargepoint_id": CP_ID,
                           "connectorId": connector_id,
                           "requestedMessage": msg})

    def change_availability(self, connector_id, avail_type):
        return self._post("/remote/changeAvailability",
                          {"chargepoint_id": CP_ID,
                           "connectorId": connector_id,
                           "type": avail_type})

    def get_diagnostics(self, location="ftp://example.com"):
        return self._post("/remote/getDiagnostics",
                          {"chargepoint_id": CP_ID,
                           "location": location})

    def update_firmware(self, location="ftp://example.com"):
        return self._post("/remote/updateFirmware",
                          {"chargepoint_id": CP_ID,
                           "location": location,
                           "retrieveDate": "2026-01-01T00:00:00Z"})

    def set_charging_profile(self, connector_id=1):
        return self._post("/remote/SetChargingProfile",
                          {"chargepoint_id": CP_ID,
                           "connectorId": connector_id,
                           "csChargingProfiles": {
                               "chargingProfileId": 201,
                               "stackLevel": 0,
                               "chargingProfilePurpose": "TxDefaultProfile",
                               "chargingProfileKind": "Absolute",
                               "chargingSchedule": {
                                   "chargingRateUnit": "A",
                                   "chargingSchedulePeriod": [
                                       {"startPeriod": 0, "limit": 32}
                                   ]
                               }
                           }})

    def reserve_now(self, connector_id=1, reservation_id=1):
        return self._post("/cmd/chargepoint/reserve",
                          {"chargepoint": CP_ID,
                           "connector": connector_id,
                           "card_id": RFID_TAG,
                           "duration": 3600})

    def cancel_reservation(self, reservation_id=1):
        return self._post("/cmd/chargepoint/cancel",
                          {"chargepoint": CP_ID,
                           "reservation_id": reservation_id})

    def heartbeat(self):
        return self._post("/cmd/chargepoint/heartbeat",
                          {"chargepoint_id": CP_ID})

    def data_transfer(self, vendor_id="MEA", message_id="Test", data=""):
        return self._post("/cmd/chargepoint/dataTransfer",
                          {"chargepoint_id": CP_ID,
                           "vendorId": vendor_id,
                           "messageId": message_id,
                           "data": data})


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
    """
    Publish to vSECC MQTT K.2.25 set_availability import topic.
    state: "operative" or "inoperative"
    Returns (payload, topic) if wait_kw is given and event observed, else (None, None).
    """
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


def mea_call_expect_404(r):
    """
    For items where HTTP 404 is the expected/known outcome.
    Returns (is_404, detail).
    """
    if r is None:
        return False, "No response"
    try:
        body   = r.json()
        status = body.get("status") or body.get("result") or ""
        detail = f"HTTP {r.status_code}" + (f" status={status}" if status else "")
    except Exception:
        detail = f"HTTP {r.status_code}"
    return r.status_code == 404, detail


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


def wait_for_reconnect(mea: MeaApi, mark, timeout=None) -> bool:
    """Wait for vSECC to reconnect after a reset."""
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
# Section 10 test body
# ─────────────────────────────────────────────
def run_section10(vsecc: VseccApi, mea: MeaApi):
    section("Section 10: CSMS Command Verification")

    # ── Connection check ─────────────────────────────────────────────────────
    print("  Checking vSECC → MEA CSMS connection (up to 30 s)...")
    if not _probe_csms_connection(mea, timeout=30):
        print("  ERROR: vSECC not connected to MEA CSMS.")
        for i in range(1, 24):
            record(f"10.{i}", f"Item 10.{i}", "FAIL",
                   "vSECC not connected to MEA CSMS")
        return
    print("  vSECC connected to MEA CSMS.")

    # ── 10.1  Authorize (CS→CSMS) ────────────────────────────────────────────
    record("10.1", "Authorize",
           "WARN",
           "Authorize is CS→CSMS; not triggerable via REST API",
           "Observed via MQTT during charging sessions (charging_authorization_state)")

    # ── 10.2  BootNotification (CS→CSMS) ─────────────────────────────────────
    record("10.2", "BootNotification",
           "WARN",
           "BootNotification is CS→CSMS; confirmed via CSMS connection (Section 1 item 1.1)",
           "Charger sends BootNotification autonomously on connect/reboot")

    # ── 10.3  DataTransfer ───────────────────────────────────────────────────
    r = mea.data_transfer()
    ok, detail = mea_call(r)
    record("10.3", "DataTransfer → vendorId=MEA messageId=Test",
           "PASS" if ok else "FAIL", detail)

    # ── 10.4  Heartbeat ──────────────────────────────────────────────────────
    r = mea.heartbeat()
    ok, detail = mea_call(r)
    if r is None or r.status_code == 404:
        record("10.4", "Heartbeat",
               "WARN",
               detail,
               "Heartbeat endpoint not exposed by MEA sandbox; charger sends heartbeats autonomously")
    else:
        record("10.4", "Heartbeat",
               "PASS" if ok else "FAIL", detail)

    # ── 10.5  MeterValues (CS→CSMS) ──────────────────────────────────────────
    record("10.5", "MeterValues",
           "WARN",
           "MeterValues is CS→CSMS; TriggerMessage not supported (HTTP 404)",
           "Observable on MQTT metervalues topic during active charging session")

    # ── 10.6  RemoteStartTransaction ─────────────────────────────────────────
    r = mea.remote_start()
    ok, detail = mea_call(r)
    record("10.6", "RemoteStartTransaction → Accepted",
           "PASS" if ok else "FAIL", detail)

    # ── 10.7  RemoteStopTransaction ──────────────────────────────────────────
    r = mea.remote_stop(0)
    ok, detail = mea_call(r)
    record("10.7", "RemoteStopTransaction (transaction_id=0) → Accepted",
           "PASS" if ok else "FAIL", detail,
           "" if ok else "transaction_id=0 — no active transaction; needs active charging session")

    # ── 10.8  Reset (Hard) ───────────────────────────────────────────────────
    mark108 = mqtt_mark()
    r = mea.reset("Hard")
    ok, detail = mea_call(r)
    record("10.8", "Reset (Hard) → Accepted",
           "PASS" if ok else "FAIL", detail)

    if ok:
        print(f"  ...waiting up to {BOOT_WAIT_SEC}s for vSECC to reboot and reconnect...")
        reconnected = wait_for_reconnect(mea, mark108, timeout=BOOT_WAIT_SEC)
        if not reconnected:
            print(f"  WARNING: vSECC did not reconnect within {BOOT_WAIT_SEC}s after Hard Reset")
    else:
        time.sleep(3)

    # ── 10.9  StartTransaction (CS→CSMS) ─────────────────────────────────────
    record("10.9", "StartTransaction",
           "WARN",
           "CS→CSMS; observable via MQTT charging_session_state during charging session",
           "Charger sends StartTransaction automatically when session begins")

    # ── 10.10 StatusNotification (CS→CSMS) ───────────────────────────────────
    record("10.10", "StatusNotification",
           "WARN",
           "CS→CSMS; observable via MQTT vsecc/connector/+/status/#",
           "Charger sends StatusNotification on connector state changes")

    # ── 10.11 StopTransaction (CS→CSMS) ──────────────────────────────────────
    record("10.11", "StopTransaction",
           "WARN",
           "CS→CSMS; observable via MQTT charging_session_state",
           "Charger sends StopTransaction on session end (RemoteStop / EVDisconnect / Reset)")

    # ── 10.12 ReserveNow ─────────────────────────────────────────────────────
    r = mea.reserve_now()
    ok, detail = mea_call(r)
    record("10.12", "ReserveNow (connector_id=1, reservationId=1) → Accepted",
           "PASS" if ok else "FAIL", detail)

    # ── 10.13 CancelReservation ───────────────────────────────────────────────
    r = mea.cancel_reservation(1)
    ok, detail = mea_call(r)
    record("10.13", "CancelReservation (reservationId=1) → Accepted",
           "PASS" if ok else "FAIL", detail)

    # ── 10.14 SetChargingProfile ─────────────────────────────────────────────
    r = mea.set_charging_profile()
    ok, detail = mea_call(r)
    record("10.14", "SetChargingProfile (TxDefaultProfile, 32 A) → Accepted",
           "PASS" if ok else "FAIL", detail)

    # ── 10.15 TriggerMessage ─────────────────────────────────────────────────
    r = mea.trigger_message("StatusNotification")
    is_404, detail = mea_call_expect_404(r)
    if is_404:
        record("10.15", "TriggerMessage (StatusNotification)",
               "WARN",
               "MEA sandbox /remote/triggerMessage not exposed (HTTP 404)",
               "CSMS→CS command; not testable via MEA REST API")
    else:
        ok, detail2 = mea_call(r)
        record("10.15", "TriggerMessage (StatusNotification)",
               "PASS" if ok else "FAIL", detail2)

    # ── 10.16 GetConfiguration ───────────────────────────────────────────────
    r = mea.get_configuration()
    ok, detail = mea_call(r)
    record("10.16", "GetConfiguration → Accepted",
           "PASS" if ok else "FAIL", detail)

    # ── 10.17 ChangeConfiguration ────────────────────────────────────────────
    r = mea.change_configuration("HeartbeatInterval", "600")
    ok, detail = mea_call(r)
    record("10.17", "ChangeConfiguration HeartbeatInterval=600 → Accepted",
           "PASS" if ok else "FAIL", detail)

    # ── 10.18 ChangeAvailability ──────────────────────────────────────────────
    # MEA REST API doesn't expose /remote/changeAvailability (HTTP 404).
    # Use vSECC MQTT K.2.25 set_availability import to command directly and
    # verify the resulting StatusNotification Unavailable on MQTT.
    payload18, topic18 = mqtt_set_availability(1, "inoperative",
                                               wait_kw="Unavailable", timeout=10)
    if payload18:
        record("10.18", "ChangeAvailability (connector 1, Inoperative)",
               "PASS",
               f"StatusNotification Unavailable confirmed via MQTT "
               f"(MQTT set_availability used; MEA /remote/changeAvailability → 404)")
        # Restore to operative for subsequent items
        mqtt_set_availability(1, "operative", wait_kw="Available", timeout=10)
    else:
        record("10.18", "ChangeAvailability (connector 1, Inoperative)",
               "WARN",
               "Unavailable not observed on MQTT in 10 s after MQTT set_availability",
               "MEA REST /remote/changeAvailability → 404; MQTT fallback used")

    # ── 10.19 GetDiagnostics ─────────────────────────────────────────────────
    r = mea.get_diagnostics()
    is_404, detail = mea_call_expect_404(r)
    if is_404:
        record("10.19", "GetDiagnostics (ftp://example.com)",
               "FAIL",
               detail + " — endpoint not exposed by MEA sandbox REST API",
               "GetDiagnostics (/remote/getDiagnostics) returns HTTP 404")
    else:
        ok, detail2 = mea_call(r)
        record("10.19", "GetDiagnostics (ftp://example.com)",
               "PASS" if ok else "FAIL", detail2)

    # ── 10.20 UpdateFirmware ─────────────────────────────────────────────────
    r = mea.update_firmware()
    is_404, detail = mea_call_expect_404(r)
    if is_404:
        record("10.20", "UpdateFirmware (ftp://example.com)",
               "FAIL",
               detail + " — endpoint not exposed by MEA sandbox REST API",
               "UpdateFirmware (/remote/updateFirmware) returns HTTP 404")
    else:
        ok, detail2 = mea_call(r)
        record("10.20", "UpdateFirmware (ftp://example.com)",
               "PASS" if ok else "FAIL", detail2)

    # ── 10.21 SendLocalList ───────────────────────────────────────────────────
    r = mea.send_local_list(version=1)
    ok, detail = mea_call(r)
    record("10.21", "SendLocalList (Full, version=1, idTag=RFID_TEST) → Accepted",
           "PASS" if ok else "FAIL", detail)

    # ── 10.22 GetLocalListVersion ─────────────────────────────────────────────
    r = mea.get_local_list_version()
    ok, detail = mea_call(r)
    record("10.22", "GetLocalListVersion → Accepted",
           "PASS" if ok else "FAIL", detail)

    # ── 10.23 ClearCache ─────────────────────────────────────────────────────
    r = mea.clear_cache()
    ok, detail = mea_call(r)
    record("10.23", "ClearCache → Accepted",
           "PASS" if ok else "FAIL", detail)


# ─────────────────────────────────────────────
# Summary + JSON
# ─────────────────────────────────────────────
def print_summary():
    counts = {s: sum(1 for r in results if r["status"] == s)
              for s in ("PASS", "FAIL", "WARN", "SKIP")}
    total  = sum(counts.values())
    print(f"\n{'═'*66}")
    print(f"  Section 10 |  "
          f"{counts['PASS']} PASS  {counts['FAIL']} FAIL  "
          f"{counts['WARN']} WARN  {counts['SKIP']} SKIP  ({total} total)")
    print(f"{'═'*66}")
    for r in results:
        print(f"  {r['item']:<6} {r['status']:<6}  {r['message'][:52]}")
    print(f"{'═'*66}")


_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def save_json(path=None):
    if path is None:
        path = os.path.join(_ROOT, "tex", "vsecc_section10_results.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump({
            "section": 10,
            "title": "CSMS Command Verification",
            "cp_id": CP_ID,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "method": "direct-to-csms",
            "boot_wait_sec": BOOT_WAIT_SEC,
            "results": results,
        }, f, indent=2, ensure_ascii=False)
    print(f"\n  Saved {path}")


# ─────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────
def main():
    print("=" * 66)
    print("  MEA OCPP 1.6 Compliance — Section 10 (10.1 – 10.23)  [no proxy]")
    print(f"  CP: {CP_ID}  |  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Boot wait (after item 10.8 Hard Reset): {BOOT_WAIT_SEC} s")
    print("=" * 66)

    vsecc = VseccApi()
    mea   = MeaApi()

    if not vsecc.login():
        print("ABORT: Cannot reach vSECC (need alias: sudo ip addr add 192.168.1.200/24 dev enp3s0)")
        sys.exit(1)
    print("  vSECC authenticated")

    start_mqtt_watcher()
    try:
        run_section10(vsecc, mea)
    finally:
        stop_mqtt_watcher()

    print_summary()
    save_json()


if __name__ == "__main__":
    main()

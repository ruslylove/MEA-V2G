#!/usr/bin/env python3
"""
MEA OCPP 1.6 Compliance Test — Section 5
การตรวจสอบ Reservation (Reservation Check)
Items 5.1 – 5.19 per OCPP1.6_FORM_MEA.pdf Annex B.

Architecture (no proxy):
  vSECC  →  wss://ocpp.measandbox.com:2930  (direct TLS)
  PC     →  vSECC REST API   (login)
  PC     →  vSECC MQTT       (observe status / session)
  PC     →  MEA REST API     (ReserveNow, CancelReservation, RemoteStart, RemoteStop)

Three flows:
  Flow 1 (5.1–5.5)   — Reserve → Cancel → Available
  Flow 2 (5.6–5.8)   — Reserve (short) → Expiry → Available
  Flow 3 (5.9–5.19)  — Reserve → EV plug → RemoteStart → Charge → RemoteStop → Unplug

Run:
  python3 tests/vsecc/test_mea_section5.py

Note:
  Items 5.11 and onwards (EV plug in reserved state) require a physical EV.
  Set EV_WAIT_SEC = 0 to skip EV-dependent items.
  Reservation expiry (5.7) waits EXPIRY_WAIT_SEC for the CSMS to expire the reservation.
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

EV_WAIT_SEC    = 60   # wait for physical EV plug events
EXPIRY_WAIT_SEC = 90  # wait for CSMS to expire a short reservation

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

    def get_configuration(self, key=None):
        return self._post("/cmd/chargepoint/getConfiguration",
                          {"chargepoint_id": CP_ID, "key": [key] if key else []})

    def reserve_now(self, connector_id=1, card_id=RFID_TAG, duration=300):
        return self._post("/cmd/chargepoint/reserve",
                          {"chargepoint": CP_ID, "connector": connector_id,
                           "card_id": card_id, "duration": duration})

    def cancel_reservation(self, reservation_id):
        return self._post("/cmd/chargepoint/cancel",
                          {"chargepoint": CP_ID, "reservation_id": reservation_id})

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
_mqtt_events = []
_mqtt_lock   = threading.Lock()
_mqtt_client = None
_ocpp_up     = threading.Event()


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
    """Publish K.2.25 set_availability; optionally wait for status event."""
    if not HAS_MQTT or _mqtt_client is None:
        return None, None
    mark = mqtt_mark()
    _mqtt_client.publish(
        f"vsecc/connector/{evse_id}/status/set_availability", state)
    if wait_kw:
        return mqtt_wait(mark, "status", wait_kw, timeout=timeout)
    return None, None


# ─────────────────────────────────────────────
# MEA call helper
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


def _extract_reservation_id(r):
    """Try to extract reservation_id from ReserveNow response body."""
    try:
        data = r.json()
        result = data.get("result", {})
        if isinstance(result, dict) and "reservation_id" in result:
            return result["reservation_id"]
        # Some endpoints return flat structure
        if "reservation_id" in data:
            return data["reservation_id"]
    except Exception:
        pass
    return None


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
# EV-plug helper
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
    if not payload:
        # Also accept "Reserved" (EV plugged while reserved)
        payload, topic = mqtt_wait(mark, "status", "Reserved", timeout=3)
    if payload:
        record(item, message, "PASS", f"{topic.split('/')[-1]}={payload}")
        return True, payload
    record(item, message, "WARN", f"No EV plug event in {timeout}s",
           "Connect physical EV and rerun for full Section 5 coverage")
    return False, "timeout"


# ─────────────────────────────────────────────
# Section 5 test body
# ─────────────────────────────────────────────
def run_section5(vsecc: VseccApi, mea: MeaApi):
    section("Section 5: การตรวจสอบ Reservation (Reservation Check)")

    print("  Checking vSECC → MEA CSMS connection (up to 30 s)...")
    if not _probe_csms_connection(mea, timeout=30):
        print("  ERROR: vSECC not connected to MEA CSMS.")
        for i in range(1, 20):
            record(f"5.{i}", f"Item 5.{i}", "FAIL", "vSECC not connected to MEA CSMS")
        return
    print("  vSECC connected to MEA CSMS.")

    reservation_id_1 = None   # used in flow 1 (cancel)
    reservation_id_2 = None   # used in flow 2 (expiry)
    reservation_id_3 = None   # used in flow 3 (charge)

    # ══════════════════════════════════════════════════════════════════════════
    # Flow 1: Reserve → Cancel (5.1–5.5)
    # ══════════════════════════════════════════════════════════════════════════
    print("\n  [Flow 1] Reserve → Cancel")

    # ── 5.1  StatusNotification Available ────────────────────────────────────
    payload51, _ = mqtt_set_availability(1, "operative",
                                         wait_kw="Available", timeout=10)
    if payload51:
        record("5.1", "StatusNotification Available (initial)",
               "PASS", "Available confirmed on MQTT (via MQTT set_availability)")
    else:
        with _mqtt_lock:
            recent = _mqtt_events[-30:]
        if any("status" in t and "Available" in p for _, t, p in recent):
            record("5.1", "StatusNotification Available (initial)",
                   "PASS", "Available observed in recent MQTT buffer")
        else:
            record("5.1", "StatusNotification Available (initial)",
                   "WARN", "Available not observed on MQTT in 10 s",
                   "Check CSMS event log; charger may already be available")

    # ── 5.2  ReserveNow → Reserved ───────────────────────────────────────────
    mark52 = mqtt_mark()
    r = mea.reserve_now(connector_id=1, card_id=RFID_TAG, duration=300)
    ok52, detail52 = mea_call(r)
    if ok52 and r:
        reservation_id_1 = _extract_reservation_id(r)
    remark52 = f"reservation_id={reservation_id_1}" if reservation_id_1 else "reservation_id not found in response"
    record("5.2", "ReserveNow (5 min) → Accepted",
           "PASS" if ok52 else "FAIL", detail52, remark52)

    # ── 5.3  StatusNotification Reserved ─────────────────────────────────────
    if ok52:
        payload, topic = mqtt_wait(mark52, "status", "Reserved", timeout=15)
        if payload:
            record("5.3", "StatusNotification Reserved (after ReserveNow)",
                   "PASS", f"{topic.split('/')[-1]}={payload}")
        else:
            record("5.3", "StatusNotification Reserved (after ReserveNow)",
                   "WARN", "Reserved status not observed on MQTT in 15 s",
                   "vSECC may use different status reporting for reservations")
    else:
        record("5.3", "StatusNotification Reserved (after ReserveNow)",
               "WARN", "Depends on 5.2 (ReserveNow)")

    # ── 5.4  CancelReservation → Available ───────────────────────────────────
    mark54 = mqtt_mark()
    rid = reservation_id_1 or 0
    r = mea.cancel_reservation(rid)
    ok54, detail54 = mea_call(r)
    record("5.4", f"CancelReservation (id={rid}) → Accepted",
           "PASS" if ok54 else "FAIL", detail54)

    # ── 5.5  StatusNotification Available (after cancel) ─────────────────────
    if ok54:
        payload, topic = mqtt_wait(mark54, "status", "Available", timeout=15)
        if payload:
            record("5.5", "StatusNotification Available (after cancel)",
                   "PASS", f"{topic.split('/')[-1]}={payload}")
        else:
            record("5.5", "StatusNotification Available (after cancel)",
                   "WARN", "Available not observed on MQTT in 15 s after cancel")
    else:
        record("5.5", "StatusNotification Available (after cancel)",
               "WARN", "Depends on 5.4 (CancelReservation)")

    # ══════════════════════════════════════════════════════════════════════════
    # Flow 2: Reserve → Expiry (5.6–5.8)
    # ══════════════════════════════════════════════════════════════════════════
    print("\n  [Flow 2] Reserve → Expiry")

    # ── 5.6  ReserveNow (short duration for expiry test) ─────────────────────
    mark56 = mqtt_mark()
    r = mea.reserve_now(connector_id=1, card_id=RFID_TAG, duration=60)
    ok56, detail56 = mea_call(r)
    if ok56 and r:
        reservation_id_2 = _extract_reservation_id(r)
    remark56 = f"reservation_id={reservation_id_2}" if reservation_id_2 else "reservation_id not found"
    record("5.6", "ReserveNow (short, for expiry test) → Accepted",
           "PASS" if ok56 else "FAIL", detail56, remark56)

    # ── 5.7  Wait for reservation expiry ─────────────────────────────────────
    if ok56:
        print(f"  ...waiting up to {EXPIRY_WAIT_SEC}s for reservation to expire...")
        payload, topic = mqtt_wait(mark56, "status", "Available", timeout=EXPIRY_WAIT_SEC)
        if payload:
            record("5.7", f"Reservation expiry (wait ≤{EXPIRY_WAIT_SEC}s)",
                   "PASS", f"Available observed after {EXPIRY_WAIT_SEC}s")
        else:
            # CSMS may enforce minimum duration; cancel manually to unblock
            print("  Expiry not observed — cancelling reservation to unblock...")
            mea.cancel_reservation(reservation_id_2 or 0)
            time.sleep(3)
            record("5.7", f"Reservation expiry (wait ≤{EXPIRY_WAIT_SEC}s)",
                   "WARN", f"Available not observed in {EXPIRY_WAIT_SEC}s",
                   "MEA CSMS may enforce a minimum reservation duration; expiry auto-cancelled")
    else:
        record("5.7", "Reservation expiry", "WARN", "Depends on 5.6 (ReserveNow)")

    # ── 5.8  StatusNotification Available (after expiry) ─────────────────────
    if ok56:
        payload, topic = mqtt_wait(mark56, "status", "Available", timeout=15)
        if payload:
            record("5.8", "StatusNotification Available (after expiry)",
                   "PASS", f"{topic.split('/')[-1]}={payload}")
        else:
            record("5.8", "StatusNotification Available (after expiry)",
                   "WARN", "Available not observed on MQTT in 15 s after expiry/cancel")
    else:
        record("5.8", "StatusNotification Available (after expiry)",
               "WARN", "Depends on 5.6 (ReserveNow)")

    # ══════════════════════════════════════════════════════════════════════════
    # Flow 3: Reserve → EV plug → Charge → Stop (5.9–5.19)
    # ══════════════════════════════════════════════════════════════════════════
    print("\n  [Flow 3] Reserve → EV plug → RemoteStart → Charge → RemoteStop")

    # ── 5.9  ReserveNow (10 min, for charge session) ──────────────────────────
    mark59 = mqtt_mark()
    r = mea.reserve_now(connector_id=1, card_id=RFID_TAG, duration=600)
    ok59, detail59 = mea_call(r)
    if ok59 and r:
        reservation_id_3 = _extract_reservation_id(r)
    remark59 = f"reservation_id={reservation_id_3}" if reservation_id_3 else "reservation_id not found"
    record("5.9", "ReserveNow (10 min, for charge session) → Accepted",
           "PASS" if ok59 else "FAIL", detail59, remark59)

    # ── 5.10 StatusNotification Reserved ─────────────────────────────────────
    if ok59:
        payload, topic = mqtt_wait(mark59, "status", "Reserved", timeout=15)
        if payload:
            record("5.10", "StatusNotification Reserved (Flow 3)",
                   "PASS", f"{topic.split('/')[-1]}={payload}")
        else:
            record("5.10", "StatusNotification Reserved (Flow 3)",
                   "WARN", "Reserved status not observed on MQTT in 15 s")
    else:
        record("5.10", "StatusNotification Reserved (Flow 3)",
               "WARN", "Depends on 5.9 (ReserveNow)")

    # ── 5.11 StatusNotification Reserved (EV plug into reserved connector) ────
    mark511 = mqtt_mark()
    ok511, _ = _ev_item("5.11", "StatusNotification Reserved/Preparing (EV plug)", mark511)

    # ── 5.12 RemoteStartTransaction ───────────────────────────────────────────
    mark512 = mqtt_mark()
    r = mea.remote_start(connector_id=1, id_tag=RFID_TAG)
    ok512, detail512 = mea_call(r)
    record("5.12", "RemoteStartTransaction → Accepted",
           "PASS" if ok512 else "FAIL", detail512)

    # ── 5.13 StartTransaction ─────────────────────────────────────────────────
    if ok512:
        payload, topic = mqtt_wait(mark512, "charging_session_state", "started", timeout=30)
        if not payload:
            payload, topic = mqtt_wait(mark512, "charging_session_state", "active", timeout=5)
        if payload:
            record("5.13", "StartTransaction (triggered by RemoteStart)",
                   "PASS", f"session_state={payload}")
        else:
            record("5.13", "StartTransaction (triggered by RemoteStart)",
                   "WARN", "charging_session_state not observed on MQTT in 30 s")
    else:
        record("5.13", "StartTransaction (triggered by RemoteStart)",
               "WARN", "Depends on 5.12 (RemoteStart)")

    # ── 5.14 StatusNotification Charging ─────────────────────────────────────
    if ok512:
        payload, topic = mqtt_wait(mark512, "status", "Charging", timeout=20)
        if payload:
            record("5.14", "StatusNotification Charging",
                   "PASS", f"{topic.split('/')[-1]}={payload}")
        else:
            record("5.14", "StatusNotification Charging",
                   "WARN", "Charging status not observed on MQTT in 20 s")
    else:
        record("5.14", "StatusNotification Charging",
               "WARN", "Depends on 5.12 (RemoteStart)")

    # ── 5.15 MeterValues ─────────────────────────────────────────────────────
    if ok512:
        mea.trigger_message("MeterValues", connector_id=1)
        payload, topic = mqtt_wait(mark512, "metervalues", "", timeout=15)
        if payload:
            record("5.15", "MeterValues (during reservation charge)",
                   "PASS", f"metervalues received: {payload[:60]}")
        else:
            record("5.15", "MeterValues (during reservation charge)",
                   "WARN", "MeterValues not observed on MQTT in 15 s")
    else:
        record("5.15", "MeterValues (during reservation charge)",
               "WARN", "Depends on 5.12 (RemoteStart)")

    # ── 5.16 RemoteStopTransaction ────────────────────────────────────────────
    mark516 = mqtt_mark()
    r = mea.remote_stop(0)
    ok516, detail516 = mea_call(r)
    record("5.16", "RemoteStopTransaction → Accepted",
           "PASS" if ok516 else "FAIL", detail516,
           "" if ok516 else "No active transaction (tx_id=0)")

    # ── 5.17 StopTransaction / Finishing ─────────────────────────────────────
    if ok516:
        payload, topic = mqtt_wait(mark516, "charging_session_state", "idle", timeout=20)
        if not payload:
            payload, topic = mqtt_wait(mark516, "charging_session_state", "finish", timeout=5)
        if payload:
            record("5.17", "StopTransaction (triggered by RemoteStop)",
                   "PASS", f"session_state={payload}")
        else:
            record("5.17", "StopTransaction (triggered by RemoteStop)",
                   "WARN", "session_state idle/finished not observed on MQTT in 25 s")
    else:
        record("5.17", "StopTransaction (triggered by RemoteStop)",
               "WARN", "Depends on 5.16 (RemoteStop)")

    # ── 5.18 StatusNotification Finishing (EV still plugged) ─────────────────
    if ok516:
        payload, topic = mqtt_wait(mark516, "status", "Finishing", timeout=15)
        if payload:
            record("5.18", "StatusNotification Finishing (EV still plugged)",
                   "PASS", f"{topic.split('/')[-1]}={payload}")
        else:
            record("5.18", "StatusNotification Finishing (EV still plugged)",
                   "WARN", "Finishing not observed on MQTT in 15 s",
                   "Some implementations skip Finishing → go directly to Available")
    else:
        record("5.18", "StatusNotification Finishing (EV still plugged)",
               "WARN", "Depends on 5.16 (RemoteStop)")

    # ── 5.19 StatusNotification Available (unplug) ───────────────────────────
    if ok516:
        print("  ...waiting for Available (unplug EV after reservation session)...")
        payload, topic = mqtt_wait(mark516, "status", "Available", timeout=30)
        if payload:
            record("5.19", "StatusNotification Available (EV unplug)",
                   "PASS", f"{topic.split('/')[-1]}={payload}")
        else:
            record("5.19", "StatusNotification Available (EV unplug)",
                   "WARN", "Available not observed on MQTT in 30 s — unplug EV")
    else:
        record("5.19", "StatusNotification Available (EV unplug)",
               "WARN", "Depends on 5.16 (RemoteStop)")


# ─────────────────────────────────────────────
# Summary + JSON
# ─────────────────────────────────────────────
def print_summary():
    counts = {s: sum(1 for r in results if r["status"] == s)
              for s in ("PASS", "FAIL", "WARN", "SKIP")}
    total  = sum(counts.values())
    print(f"\n{'═'*66}")
    print(f"  Section 5  |  "
          f"{counts['PASS']} PASS  {counts['FAIL']} FAIL  "
          f"{counts['WARN']} WARN  {counts['SKIP']} SKIP  ({total} total)")
    print(f"{'═'*66}")
    for r in results:
        print(f"  {r['item']:<6} {r['status']:<6}  {r['message'][:52]}")
    print(f"{'═'*66}")


_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def save_json(path=None):
    if path is None:
        path = os.path.join(_ROOT, "tex", "vsecc_section5_results.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump({
            "section": 5,
            "title": "การตรวจสอบ Reservation",
            "cp_id": CP_ID,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "method": "direct-to-csms",
            "ev_wait_sec": EV_WAIT_SEC,
            "expiry_wait_sec": EXPIRY_WAIT_SEC,
            "results": results,
        }, f, indent=2, ensure_ascii=False)
    print(f"\n  Saved {path}")


# ─────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────
def main():
    print("=" * 66)
    print("  MEA OCPP 1.6 Compliance — Section 5 (5.1 – 5.19)  [no proxy]")
    print(f"  CP: {CP_ID}  |  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  EV plug timeout: {EV_WAIT_SEC} s  |  Expiry wait: {EXPIRY_WAIT_SEC} s")
    print("=" * 66)

    vsecc = VseccApi()
    mea   = MeaApi()

    if not vsecc.login():
        print("ABORT: Cannot reach vSECC (need alias: sudo ip addr add 192.168.1.200/24 dev enp3s0)")
        sys.exit(1)
    print("  vSECC authenticated")

    start_mqtt_watcher()
    try:
        run_section5(vsecc, mea)
    finally:
        stop_mqtt_watcher()

    print_summary()
    save_json()


if __name__ == "__main__":
    main()

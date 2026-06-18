#!/usr/bin/env python3
"""
vSECC OCPP Compliance Test
Configures the Vector vSECC to connect to MEA CSMS as an OCPP 1.6 client,
then exercises OCPP commands via the MEA REST API to validate compliance.
"""

import json
import time
import sys
import threading
import requests
from requests.auth import HTTPDigestAuth
from datetime import datetime, timedelta

try:
    import paho.mqtt.client as mqtt
    HAS_MQTT = True
except ImportError:
    HAS_MQTT = False
    print("WARN: paho-mqtt not available, MQTT monitoring disabled")

# ─────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────
VSECC_BASE     = "http://192.168.1.166/api"
VSECC_USER     = "admin"
VSECC_PASS     = "admin"
MQTT_HOST      = "192.168.1.166"
MQTT_PORT      = 1883
MQTT_USER      = "vector"
MQTT_PASS      = "vector"

CP_ID          = "rddQC4000001"
# vSECC connects directly to MEA CSMS over TLS (no local proxy needed).
CSMS_BASE_URL  = "wss://ocpp.measandbox.com:2930/EV/Srv/JSON/1.6"
CSMS_SEC_PROFILE = "1"  # 1 = TLS for wss://

MEA_API_BASE   = "https://ocppapi.measandbox.com/EV"
MEA_USER       = "meaev.api.dev"
MEA_PASS_START = "Bh9GKYvSBc9KkbJ"
MEA_PASS_DEF   = "U`?d3~C_Se77CrdsG[l#hq1)J_2$FA1D"

# varIds from GET /api/variables
VARID_CSMS_URL    = "28f51fe0"
VARID_IDENTITY    = "c9d0e1f2"
VARID_URL_IDENT   = "7e8f9a0b"
VARID_SEC_PROFILE = "e5f6a7b8"
VARID_BASIC_PASS  = "3a4b5c6d"
VARID_BACKEND_ON  = "cb9c8312"

PASS_FAIL = {True: "PASS", False: "FAIL"}


# ─────────────────────────────────────────────
# vSECC REST helpers
# ─────────────────────────────────────────────
class VseccApi:
    def __init__(self):
        self.token = None

    def login(self):
        r = requests.post(f"{VSECC_BASE}/login",
                          json={"name": VSECC_USER, "password": VSECC_PASS},
                          timeout=10)
        self.token = r.text.strip().strip('"')
        return bool(self.token)

    def _headers(self):
        return {"Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json"}

    def get_var(self, var_id):
        r = requests.get(f"{VSECC_BASE}/variables/{var_id}",
                         headers=self._headers(), timeout=10)
        return r.json() if r.ok else None

    def set_var(self, var_obj, new_value):
        body = dict(var_obj)
        body["value"] = new_value
        r = requests.put(f"{VSECC_BASE}/variables/{var_obj['varId']}",
                         headers=self._headers(),
                         data=json.dumps(body), timeout=10)
        return r.ok, r.text

    def restart(self):
        r = requests.post(f"{VSECC_BASE}/system/restart",
                          headers=self._headers(),
                          data='"vsecc"', timeout=10)
        return r.ok


# ─────────────────────────────────────────────
# MEA CSMS API helpers
# ─────────────────────────────────────────────
class MeaApi:
    def _post(self, path, payload, password=MEA_PASS_DEF):
        try:
            r = requests.post(f"{MEA_API_BASE}{path}", json=payload,
                              auth=HTTPDigestAuth(MEA_USER, password),
                              timeout=15)
            return r
        except Exception as e:
            print(f"  MEA API error: {e}")
            return None

    def get_configuration(self, key=None):
        return self._post("/cmd/chargepoint/getConfiguration",
                          {"chargepoint_id": CP_ID, "key": [key] if key else []})

    def change_configuration(self, key, value):
        return self._post("/remote/changeConfiguration",
                          {"chargepoint_id": CP_ID, "key": key, "value": value})

    def remote_start(self, connector_id=1, id_tag="RFID_TEST"):
        return self._post("/cmd/chargepoint/remoteStart",
                          {"chargepoint_id": CP_ID, "connector_id": connector_id,
                           "card_id": id_tag},
                          password=MEA_PASS_START)

    def remote_stop(self, tx_id):
        return self._post("/cmd/chargepoint/remoteStop",
                          {"chargepoint_id": CP_ID, "transaction_id": tx_id})

    def reserve(self, connector=1, duration=15, id_tag="RES_TAG"):
        return self._post("/cmd/chargepoint/reserve",
                          {"chargepoint": CP_ID, "connector": connector,
                           "duration": duration, "card_id": id_tag})

    def cancel_reservation(self, res_id=1):
        return self._post("/cmd/chargepoint/cancel",
                          {"chargepoint": CP_ID, "reservation_id": res_id})

    def set_charging_profile(self, connector_id=1, profile=None):
        if profile is None:
            now = datetime.utcnow().isoformat() + "Z"
            tomorrow = (datetime.utcnow() + timedelta(days=1)).isoformat() + "Z"
            profile = {
                "chargingProfileId": 201, "stackLevel": 1,
                "chargingProfilePurpose": "TxDefaultProfile",
                "chargingProfileKind": "Absolute",
                "validFrom": now, "validTo": tomorrow,
                "chargingSchedule": {
                    "duration": 600, "startSchedule": now,
                    "chargingRateUnit": "A", "minChargingRate": 6,
                    "chargingSchedulePeriod": [{"startPeriod": 0, "limit": 16.0, "numberPhases": 3}]
                }
            }
        return self._post("/remote/SetChargingProfile",
                          {"chargepoint_id": CP_ID, "connectorId": connector_id,
                           "csChargingProfiles": profile})

    def reset(self, reset_type="Soft"):
        return self._post("/remote/reset",
                          {"chargepoint": CP_ID, "reset_type": reset_type})

    def trigger_message(self, message, connector_id=1):
        return self._post("/remote/triggerMessage",
                          {"chargepoint_id": CP_ID, "connectorId": connector_id,
                           "requestedMessage": message})


# ─────────────────────────────────────────────
# MQTT monitor (background thread)
# ─────────────────────────────────────────────
mqtt_log = []
mqtt_connected = threading.Event()

def start_mqtt_monitor():
    if not HAS_MQTT:
        return None

    def on_connect(client, userdata, flags, rc):
        if rc == 0:
            client.subscribe("vsecc/#")

    def on_message(client, userdata, msg):
        try:
            payload = msg.payload.decode()
        except Exception:
            payload = repr(msg.payload)
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        entry = f"[MQTT {ts}] {msg.topic} = {payload}"
        mqtt_log.append(entry)
        print(f"  {entry}")
        # Signal when backend connection is up
        if "backend" in msg.topic and "connect" in payload.lower():
            mqtt_connected.set()

    client = mqtt.Client()
    client.username_pw_set(MQTT_USER, MQTT_PASS)
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
    client.loop_start()
    return client


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────
def check(label, condition, detail=""):
    status = PASS_FAIL[bool(condition)]
    suffix = f"  ({detail})" if detail else ""
    print(f"  [{status}] {label}{suffix}")
    return condition


def section(title):
    print(f"\n{'─'*60}")
    print(f"  {title}")
    print(f"{'─'*60}")


def wait_ocpp_online(mea_api, timeout=60):
    """Wait for vSECC OCPP connection via MQTT status, then confirm via MEA API."""
    print(f"  Waiting up to {timeout}s for vSECC to connect...")
    if not HAS_MQTT:
        # Fallback: poll MEA API
        deadline = time.time() + timeout
        while time.time() < deadline:
            r = mea_api.get_configuration("HeartbeatInterval")
            if r and r.status_code == 200:
                print("  vSECC is ONLINE at MEA CSMS")
                return True
            time.sleep(5)
        print(f"  TIMEOUT")
        return False

    connected = threading.Event()
    def on_connect(c, u, f, rc): c.subscribe("vsecc/ocpp_connection_status")
    def on_message(c, u, msg):
        if msg.payload.decode().strip() == "connected":
            connected.set()

    mc = mqtt.Client()
    mc.username_pw_set(MQTT_USER, MQTT_PASS)
    mc.on_connect = on_connect
    mc.on_message = on_message
    mc.connect(MQTT_HOST, MQTT_PORT)
    mc.loop_start()
    result = connected.wait(timeout=timeout)
    mc.loop_stop()
    mc.disconnect()

    if result:
        print("  vSECC OCPP status: connected (via MQTT)")
        # Give a moment for MEA CSMS to register the boot
        time.sleep(3)
        return True
    print(f"  TIMEOUT: vSECC did not connect within {timeout}s")
    return False


# ─────────────────────────────────────────────
# Main test
# ─────────────────────────────────────────────
def main():
    print("=" * 60)
    print("  vSECC OCPP Compliance Test vs MEA CSMS")
    print(f"  CP ID : {CP_ID}")
    print(f"  CSMS  : {CSMS_BASE_URL}/{CP_ID} (direct)")
    print("=" * 60)

    vsecc = VseccApi()
    mea = MeaApi()

    # ── MQTT monitor ─────────────────────────────────────────
    mqtt_client = start_mqtt_monitor()
    if mqtt_client:
        print("\n[MQTT monitor started]")

    # ── 0. vSECC Login ───────────────────────────────────────
    section("0. vSECC Authentication")
    ok = vsecc.login()
    if not check("vSECC login", ok):
        print("  Cannot continue without vSECC access")
        sys.exit(1)

    # ── 1. Configure OCPP on vSECC ───────────────────────────
    section("1. Configure vSECC OCPP → MEA CSMS")

    configs = [
        (VARID_CSMS_URL,    CSMS_BASE_URL,    "CSMS URL (direct)"),
        (VARID_URL_IDENT,   CP_ID,            "URL identity overwrite (CP ID)"),
        (VARID_IDENTITY,    CP_ID,            "Identity (CP ID)"),
        (VARID_BASIC_PASS,  "",               "Clear basic auth password"),
        (VARID_SEC_PROFILE, CSMS_SEC_PROFILE, "Security profile (1=TLS for wss://)"),
        (VARID_BACKEND_ON,  "true",           "Backend communication activated"),
    ]

    for var_id, new_val, label in configs:
        var = vsecc.get_var(var_id)
        if var is None:
            check(label, False, "variable not found")
            continue
        if var.get("value") == new_val:
            print(f"  [SKIP] {label} already = {repr(new_val)}")
            continue
        ok, resp = vsecc.set_var(var, new_val)
        check(f"Set {label}", ok, f"{repr(var.get('value'))} → {repr(new_val)}")

    # Verify final values
    print("\n  Verification:")
    url_var = vsecc.get_var(VARID_CSMS_URL)
    id_var  = vsecc.get_var(VARID_URL_IDENT)
    check("CSMS URL set correctly",   url_var and url_var.get("value") == CSMS_BASE_URL,
          url_var.get("value") if url_var else "N/A")
    check("CP identity set correctly", id_var and id_var.get("value") == CP_ID,
          id_var.get("value") if id_var else "N/A")

    # ── 2. Restart vSECC ─────────────────────────────────────
    section("2. Restart vSECC OCPP Stack")
    print("  Sending restart request...")
    ok = vsecc.restart()
    check("Restart accepted", ok)
    if ok:
        print("  Waiting 15s for vSECC to reboot...")
        time.sleep(15)

    # ── 3. Wait for OCPP connection ───────────────────────────
    section("3. Wait for OCPP Connection to MEA CSMS")
    online = wait_ocpp_online(mea, timeout=90)
    if not check("vSECC connected to MEA CSMS", online):
        print("\n  Check: Does vSECC reach ocpp.measandbox.com?")
        print("  Check: Is the vSECC internet route configured?")
        print("  MQTT log (last 10 entries):")
        for line in mqtt_log[-10:]:
            print(f"    {line}")
        sys.exit(1)

    # ── 4. Boot & Status ─────────────────────────────────────
    section("4. Boot & Heartbeat Verification (via CSMS GetConfig)")
    r = mea.get_configuration("HeartbeatInterval")
    check("GetConfiguration(HeartbeatInterval) → 200", r and r.status_code == 200,
          f"HTTP {r.status_code}" if r else "None")

    r = mea.get_configuration("V2GMode")
    check("GetConfiguration(V2GMode) → 200", r and r.status_code == 200,
          f"HTTP {r.status_code}" if r else "None")
    if r and r.ok:
        data = r.json()
        keys = {k["key"]: k.get("value") for k in data.get("configurationKey", [])}
        v2g_val = keys.get("V2GMode", "not present")
        print(f"    V2GMode = {v2g_val}")

    # ── 5. Configuration Control ──────────────────────────────
    section("5. Configuration Control")
    r = mea.change_configuration("V2GMode", "true")
    check("ChangeConfiguration(V2GMode=true) → 200", r and r.status_code == 200,
          f"HTTP {r.status_code}" if r else "None")
    time.sleep(1)

    r = mea.change_configuration("MEA_V2G_PowerDemand", "3000")
    check("ChangeConfiguration(MEA_V2G_PowerDemand=3000) → 200", r and r.status_code == 200,
          f"HTTP {r.status_code}" if r else "None")
    time.sleep(1)

    r = mea.change_configuration("MEA_V2G_PowerDemand", "-3000")
    check("ChangeConfiguration(MEA_V2G_PowerDemand=-3000/V2G discharge) → 200",
          r and r.status_code == 200, f"HTTP {r.status_code}" if r else "None")
    time.sleep(1)

    # ── 6. Remote Start/Stop ──────────────────────────────────
    section("6. Remote Start/Stop")
    r = mea.remote_start(connector_id=1, id_tag="RFID_TEST")
    check("RemoteStartTransaction → 200", r and r.status_code == 200,
          f"HTTP {r.status_code}" if r else "None")
    time.sleep(5)

    r = mea.remote_stop(tx_id=1)
    check("RemoteStopTransaction → 200", r and r.status_code == 200,
          f"HTTP {r.status_code}" if r else "None")
    time.sleep(2)

    # ── 7. Reservation ────────────────────────────────────────
    section("7. Reservation")
    r = mea.reserve(connector=1, duration=10, id_tag="RES_TAG")
    check("ReserveNow → 200", r and r.status_code == 200,
          f"HTTP {r.status_code}" if r else "None")
    time.sleep(1)

    r = mea.cancel_reservation(res_id=1)
    check("CancelReservation → 200", r and r.status_code == 200,
          f"HTTP {r.status_code}" if r else "None")
    time.sleep(1)

    # ── 8. Smart Charging ─────────────────────────────────────
    section("8. Smart Charging")
    r = mea.set_charging_profile(connector_id=1)
    check("SetChargingProfile → 200", r and r.status_code == 200,
          f"HTTP {r.status_code}" if r else "None")
    time.sleep(1)

    # ── 9. Trigger Messages ───────────────────────────────────
    section("9. Trigger Messages")
    for msg in ["Heartbeat", "MeterValues", "StatusNotification"]:
        r = mea.trigger_message(msg)
        check(f"TriggerMessage({msg}) → 200", r and r.status_code == 200,
              f"HTTP {r.status_code}" if r else "None")
        time.sleep(1)

    # ── 10. Reset ─────────────────────────────────────────────
    section("10. Reset")
    r = mea.reset("Soft")
    check("Reset(Soft) → 200", r and r.status_code == 200,
          f"HTTP {r.status_code}" if r else "None")

    # ── Summary ───────────────────────────────────────────────
    section("Test Complete")
    print(f"  MQTT events captured: {len(mqtt_log)}")
    if mqtt_log:
        print("  Recent MQTT events:")
        for line in mqtt_log[-15:]:
            print(f"    {line}")

    if mqtt_client:
        mqtt_client.loop_stop()
        mqtt_client.disconnect()


if __name__ == "__main__":
    main()

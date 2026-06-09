#!/usr/bin/env python3
"""
MEA OCPP 1.6 Compliance Test — Section 11
Performance — Reconnect Time (11.1 – 11.2)

Architecture (no proxy):
  vSECC  →  wss://ocpp.measandbox.com:2930  (direct TLS)
  PC     →  vSECC REST API   (login)
  PC     →  vSECC MQTT       (observe ocpp_connection_status=connected)
  PC     →  MEA REST API     (Hard Reset to trigger reboot)

Items:
  11.1  Reconnect Time — Single measurement.
        Send Hard Reset; measure time from reset call to MQTT
        ocpp_connection_status=connected OR REST probe success.
        PASS if < 90 s, WARN if 90–180 s, FAIL if never reconnected.

  11.2  Average Performance — Repeat measurement 3 times (5 s apart).
        Record individual timings, average, min, max.
        PASS if average < 90 s.

Run:
  python3 tests/vsecc/test_mea_section11.py
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

# Seconds to wait for reconnect after each Hard Reset
BOOT_WAIT_SEC = 90
# Number of repetitions for item 11.2
REPEAT_COUNT  = 3
# Seconds between repetitions
REPEAT_DELAY  = 5
# Pass threshold (seconds)
PASS_THRESHOLD_SEC  = 90
# Warn threshold (seconds) — above this is FAIL if never reconnected
WARN_THRESHOLD_SEC  = 180

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
                          {"chargepoint_id": CP_ID, "key": [key] if key else []})


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
# Single reconnect measurement
# ─────────────────────────────────────────────
def _measure_reconnect(mea: MeaApi, attempt_label: str) -> float:
    """
    1. Clear _ocpp_up.
    2. Record start time.
    3. Send Hard Reset.
    4. Wait for MQTT ocpp_connection_status=connected OR REST probe HTTP 200.
    5. Return elapsed seconds, or BOOT_WAIT_SEC+1 if timed out.
    """
    _ocpp_up.clear()
    mark = mqtt_mark()

    # Send Hard Reset
    r = mea.reset("Hard")
    ok, detail = mea_call(r)
    if not ok:
        print(f"  [{attempt_label}] Hard Reset failed: {detail}")
        return float(BOOT_WAIT_SEC + 1)

    t_start = time.time()
    print(f"  [{attempt_label}] Hard Reset sent ({detail}); timing reconnect...")

    deadline = t_start + BOOT_WAIT_SEC
    while time.time() < deadline:
        # Check MQTT for fresh reconnect event
        with _mqtt_lock:
            for ts, topic, payload in _mqtt_events[mark:]:
                if "ocpp_connection_status" in topic and "connected" == payload.strip():
                    elapsed = ts - t_start
                    _ocpp_up.set()
                    print(f"  [{attempt_label}] Reconnected via MQTT in {elapsed:.1f} s")
                    return elapsed

        # Also probe REST
        try:
            resp = mea.get_configuration()
            if resp and resp.status_code == 200:
                elapsed = time.time() - t_start
                _ocpp_up.set()
                print(f"  [{attempt_label}] Reconnected via REST in {elapsed:.1f} s")
                return elapsed
        except Exception:
            pass

        time.sleep(1)

    elapsed = time.time() - t_start
    print(f"  [{attempt_label}] No reconnect in {elapsed:.1f} s (timeout)")
    return elapsed


# ─────────────────────────────────────────────
# Section 11 test body
# ─────────────────────────────────────────────
def run_section11(vsecc: VseccApi, mea: MeaApi):
    section("Section 11: Performance — Reconnect Time")

    # ── Connection check ─────────────────────────────────────────────────────
    print("  Checking vSECC → MEA CSMS connection (up to 30 s)...")
    if not _probe_csms_connection(mea, timeout=30):
        print("  ERROR: vSECC not connected to MEA CSMS.")
        record("11.1", "Reconnect Time — single measurement", "FAIL",
               "vSECC not connected to MEA CSMS")
        record("11.2", "Average Reconnect Time — 3 measurements", "FAIL",
               "vSECC not connected to MEA CSMS")
        return
    print("  vSECC connected to MEA CSMS.")

    # ══════════════════════════════════════════════════════════════════════════
    # 11.1  Single reconnect measurement
    # ══════════════════════════════════════════════════════════════════════════
    section("11.1 Reconnect Time — Single Measurement")

    elapsed_11_1 = _measure_reconnect(mea, "11.1")

    if elapsed_11_1 < PASS_THRESHOLD_SEC:
        status_11_1  = "PASS"
        detail_11_1  = f"Reconnected in {elapsed_11_1:.1f} s (< {PASS_THRESHOLD_SEC} s threshold)"
    elif elapsed_11_1 < WARN_THRESHOLD_SEC:
        status_11_1  = "WARN"
        detail_11_1  = (f"Reconnected in {elapsed_11_1:.1f} s "
                        f"({PASS_THRESHOLD_SEC}–{WARN_THRESHOLD_SEC} s — marginal)")
    else:
        status_11_1  = "FAIL"
        detail_11_1  = (f"No reconnect in {elapsed_11_1:.1f} s "
                        f"(> {WARN_THRESHOLD_SEC} s threshold)")

    record("11.1", "Reconnect Time after Hard Reset",
           status_11_1, detail_11_1,
           f"time_ms={int(elapsed_11_1 * 1000)}")

    # Allow the vSECC to stabilize before the repeat measurements
    print(f"  Waiting {REPEAT_DELAY} s before repeat measurements...")
    time.sleep(REPEAT_DELAY)

    # ══════════════════════════════════════════════════════════════════════════
    # 11.2  Average performance — 3 measurements
    # ══════════════════════════════════════════════════════════════════════════
    section(f"11.2 Average Reconnect Time — {REPEAT_COUNT} Measurements")

    timings = []
    for i in range(1, REPEAT_COUNT + 1):
        elapsed = _measure_reconnect(mea, f"11.2 run {i}/{REPEAT_COUNT}")
        timings.append(elapsed)
        if i < REPEAT_COUNT:
            print(f"  Waiting {REPEAT_DELAY} s before next measurement...")
            time.sleep(REPEAT_DELAY)

    avg_t = sum(timings) / len(timings)
    min_t = min(timings)
    max_t = max(timings)

    timings_str = ", ".join(f"{t:.1f}" for t in timings)
    detail_11_2 = (f"avg={avg_t:.1f}s min={min_t:.1f}s max={max_t:.1f}s "
                   f"[{timings_str}]")

    if avg_t < PASS_THRESHOLD_SEC:
        status_11_2 = "PASS"
    elif avg_t < WARN_THRESHOLD_SEC:
        status_11_2 = "WARN"
    else:
        status_11_2 = "FAIL"

    # Build individual timing remark
    timing_remark_parts = [f"run{i+1}={t*1000:.0f}ms" for i, t in enumerate(timings)]
    timing_remark = "; ".join(timing_remark_parts)

    record("11.2",
           f"Average Reconnect Time ({REPEAT_COUNT} measurements)",
           status_11_2, detail_11_2, timing_remark)


# ─────────────────────────────────────────────
# Summary + JSON
# ─────────────────────────────────────────────
def print_summary():
    counts = {s: sum(1 for r in results if r["status"] == s)
              for s in ("PASS", "FAIL", "WARN", "SKIP")}
    total  = sum(counts.values())
    print(f"\n{'═'*66}")
    print(f"  Section 11 |  "
          f"{counts['PASS']} PASS  {counts['FAIL']} FAIL  "
          f"{counts['WARN']} WARN  {counts['SKIP']} SKIP  ({total} total)")
    print(f"{'═'*66}")
    for r in results:
        print(f"  {r['item']:<6} {r['status']:<6}  {r['message'][:52]}")
    print(f"{'═'*66}")


_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def save_json(path=None):
    if path is None:
        path = os.path.join(_ROOT, "tex", "vsecc_section11_results.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump({
            "section": 11,
            "title": "Performance -- Reconnect Time",
            "cp_id": CP_ID,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "method": "direct-to-csms",
            "boot_wait_sec": BOOT_WAIT_SEC,
            "pass_threshold_sec": PASS_THRESHOLD_SEC,
            "warn_threshold_sec": WARN_THRESHOLD_SEC,
            "repeat_count": REPEAT_COUNT,
            "repeat_delay_sec": REPEAT_DELAY,
            "results": results,
        }, f, indent=2, ensure_ascii=False)
    print(f"\n  Saved {path}")


# ─────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────
def main():
    print("=" * 66)
    print("  MEA OCPP 1.6 Compliance — Section 11 (11.1 – 11.2)  [no proxy]")
    print(f"  CP: {CP_ID}  |  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Boot wait per attempt: {BOOT_WAIT_SEC} s")
    print(f"  Repeat count (item 11.2): {REPEAT_COUNT}  |  Delay: {REPEAT_DELAY} s")
    print(f"  PASS threshold: < {PASS_THRESHOLD_SEC} s  |  WARN: {PASS_THRESHOLD_SEC}–{WARN_THRESHOLD_SEC} s")
    print("=" * 66)

    vsecc = VseccApi()
    mea   = MeaApi()

    if not vsecc.login():
        print("ABORT: Cannot reach vSECC (need alias: sudo ip addr add 192.168.1.200/24 dev enp3s0)")
        sys.exit(1)
    print("  vSECC authenticated")

    start_mqtt_watcher()
    try:
        run_section11(vsecc, mea)
    finally:
        stop_mqtt_watcher()

    print_summary()
    save_json()


if __name__ == "__main__":
    main()

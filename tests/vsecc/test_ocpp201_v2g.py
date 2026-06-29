#!/usr/bin/env python3
"""
Proof-of-concept: OCPP 2.0.1 SetChargingProfile with negative power limit (V2G/BPT)

OCPP 1.6J has no native V2G — MEA uses a proprietary ChangeConfiguration key
(Power.Active.Import) as a workaround. OCPP 2.0.1 supports negative power limits
natively in SetChargingProfile. This test proves the concept against the vSECC.

Usage modes
───────────
  --server-only   Start the local OCPP 2.0.1 CSMS and wait — configure vSECC
                  via its web GUI to point at ws://192.168.1.200:9201 (OCPP20,
                  security profile 0). This is the recommended mode.

  (default)       Auto-configure vSECC slot 1 via REST API, restart, run test,
                  then restore. Requires vSECC reachable at 192.168.1.166.

  --restore       Only restore vSECC slot 1 to MEA CSMS config (use if the
                  script crashed before finishing).

Manual vSECC web GUI steps (for --server-only mode)
────────────────────────────────────────────────────
  1. Open http://192.168.1.166 in a browser (admin/admin)
  2. Go to Network Configuration → Slot 1 (or any free slot)
  3. Set:
       OCPP Version    : OCPP 2.0.1
       CSMS URL        : ws://192.168.1.200:9201
       Security Profile: 0 (No TLS)
       Identity        : rddQC4000001
  4. Save and restart vSECC
  5. This script (--server-only) will accept the connection and send
     SetChargingProfile with limit=-5000 W, +3700 W, 0 W

Run:
  python3 tests/vsecc/test_ocpp201_v2g.py --server-only
  python3 tests/vsecc/test_ocpp201_v2g.py           # auto-configure
  python3 tests/vsecc/test_ocpp201_v2g.py --restore
"""

import argparse
import asyncio
import json
import logging
import sys
import time
import requests
import websockets

from datetime import datetime, timezone
from ocpp.v201 import ChargePoint as CpBase, call, call_result
from ocpp.v201.enums import (
    RegistrationStatusEnumType,
    ChargingProfilePurposeEnumType,
    ChargingProfileKindEnumType,
    ChargingRateUnitEnumType,
    ChargingProfileStatusEnumType,
)
from ocpp.routing import on

# ── Configuration ─────────────────────────────────────────────────────────────
VSECC_BASE  = "http://192.168.1.166/api"
VSECC_USER  = "admin"
VSECC_PASS  = "admin"
CP_ID       = "rddQC4000001"
LOCAL_IP    = "192.168.1.200"
LOCAL_PORT  = 9201
SERVER_URL  = f"ws://{LOCAL_IP}:{LOCAL_PORT}"

# Slot 1 variable IDs (currently active — we temporarily redirect it)
VAR_SLOT1_URL     = "28f51fe0"   # network_configuration_slot_1_ocpp_csms_url
VAR_SLOT1_VERSION = "5419d05d"   # network_configuration_slot_1_ocpp_version
VAR_SLOT1_SECPROF = "e5f6a7b8"   # network_configuration_slot_1_security_profile

# Original slot 1 values (restored after test)
ORIG_SLOT1_URL     = "wss://ocpp.measandbox.com:2930/EV/Srv/JSON/1.6"
ORIG_SLOT1_VERSION = "OCPP16"
ORIG_SLOT1_SECPROF = "1"

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("ocpp201_v2g")

results = []


def record(item, message, status, detail=""):
    tag = {"PASS": "[PASS]", "FAIL": "[FAIL]", "WARN": "[WARN]", "INFO": "[INFO]"}[status]
    print(f"  {tag} {item}  {message}" + (f"  ({detail})" if detail else ""))
    results.append({"item": item, "message": message, "status": status, "detail": detail})


# ── vSECC REST helpers ────────────────────────────────────────────────────────
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
        body = dict(obj)
        body["value"] = value
        r = requests.put(f"{VSECC_BASE}/variables/{var_id}",
                         headers=self._h(), data=json.dumps(body), timeout=10)
        return r.ok

    def restart(self):
        r = requests.post(f"{VSECC_BASE}/system/restart",
                          headers=self._h(), data='"vsecc"', timeout=10)
        return r.ok


# ── OCPP 2.0.1 mini-CSMS ─────────────────────────────────────────────────────
_charge_point: "MiniCsms201 | None" = None
_boot_received = asyncio.Event()


class MiniCsms201(CpBase):
    """Minimal OCPP 2.0.1 CSMS — accepts BootNotification, runs test commands."""

    @on("BootNotification")
    async def on_boot_notification(self, charging_station, reason, **kwargs):
        log.info(f"BootNotification  model={charging_station.get('model')}  reason={reason}")
        _boot_received.set()
        return call_result.BootNotification(
            current_time=datetime.now(timezone.utc).isoformat(),
            interval=300,
            status=RegistrationStatusEnumType.accepted,
        )

    @on("Heartbeat")
    async def on_heartbeat(self, **kwargs):
        return call_result.Heartbeat(
            current_time=datetime.now(timezone.utc).isoformat()
        )

    @on("StatusNotification")
    async def on_status_notification(self, **kwargs):
        log.info(f"StatusNotification  {kwargs}")
        return call_result.StatusNotification()

    @on("TransactionEvent")
    async def on_transaction_event(self, **kwargs):
        log.info(f"TransactionEvent  eventType={kwargs.get('event_type')}")
        return call_result.TransactionEvent()

    async def send_set_charging_profile(self, power_w: int, profile_id: int = 1):
        """Send SetChargingProfile with a single period at power_w (may be negative)."""
        profile = {
            "id": profile_id,
            "stack_level": 0,
            "charging_profile_purpose": ChargingProfilePurposeEnumType.tx_default_profile,
            "charging_profile_kind": ChargingProfileKindEnumType.absolute,
            "charging_schedule": [
                {
                    "id": profile_id,
                    "charging_rate_unit": ChargingRateUnitEnumType.watts,
                    "charging_schedule_period": [
                        {"start_period": 0, "limit": float(power_w)}
                    ],
                }
            ],
        }
        log.info(f"→ SetChargingProfile  evse_id=1  limit={power_w} W")
        try:
            resp = await self.call(call.SetChargingProfile(evse_id=1, charging_profile=profile))
            log.info(f"← SetChargingProfile response: status={resp.status}")
            return resp.status
        except Exception as e:
            log.error(f"SetChargingProfile error: {e}")
            return None


async def _csms_handler(websocket):
    global _charge_point
    path  = getattr(websocket.request, "path", "/")
    cp_id = path.lstrip("/") or CP_ID
    log.info(f"vSECC connected  path={path}  id={cp_id}")
    _charge_point = MiniCsms201(cp_id, websocket)
    await _charge_point.start()


async def _run_test_sequence(cp: MiniCsms201):
    """Wait for boot then exercise SetChargingProfile with V2G power values."""
    log.info("Waiting for BootNotification (up to 60 s)...")
    try:
        await asyncio.wait_for(_boot_received.wait(), timeout=60)
    except asyncio.TimeoutError:
        record("boot", "vSECC BootNotification", "FAIL", "No BootNotification in 60 s")
        return
    record("boot", "vSECC sent BootNotification (OCPP 2.0.1)", "PASS")

    await asyncio.sleep(1)  # let vSECC settle after boot acceptance

    # ── Test 1: Discharge  -5000 W (V2G grid←EV) ─────────────────────────────
    status = await cp.send_set_charging_profile(-5000, profile_id=1)
    item = "scp_neg"
    if status == ChargingProfileStatusEnumType.accepted:
        record(item, "SetChargingProfile limit=-5000 W (V2G discharge)", "PASS",
               f"vSECC responded: {status}")
    elif status is None:
        record(item, "SetChargingProfile limit=-5000 W (V2G discharge)", "FAIL",
               "No response / exception")
    else:
        record(item, "SetChargingProfile limit=-5000 W (V2G discharge)", "WARN",
               f"vSECC responded: {status} (not Accepted)")
    await asyncio.sleep(1)

    # ── Test 2: Normal charge  +3700 W ────────────────────────────────────────
    status = await cp.send_set_charging_profile(3700, profile_id=2)
    item = "scp_pos"
    if status == ChargingProfileStatusEnumType.accepted:
        record(item, "SetChargingProfile limit=+3700 W (normal charge)", "PASS",
               f"vSECC responded: {status}")
    elif status is None:
        record(item, "SetChargingProfile limit=+3700 W (normal charge)", "FAIL",
               "No response / exception")
    else:
        record(item, "SetChargingProfile limit=+3700 W (normal charge)", "WARN",
               f"vSECC responded: {status}")
    await asyncio.sleep(1)

    # ── Test 3: Stop / idle  0 W ──────────────────────────────────────────────
    status = await cp.send_set_charging_profile(0, profile_id=3)
    item = "scp_zero"
    if status == ChargingProfileStatusEnumType.accepted:
        record(item, "SetChargingProfile limit=0 W (idle/stop)", "PASS",
               f"vSECC responded: {status}")
    elif status is None:
        record(item, "SetChargingProfile limit=0 W (idle/stop)", "FAIL",
               "No response / exception")
    else:
        record(item, "SetChargingProfile limit=0 W (idle/stop)", "WARN",
               f"vSECC responded: {status}")


# ── vSECC configuration helpers ───────────────────────────────────────────────
def configure_slot1_for_test(api: VseccApi):
    """Redirect slot 1 (active) to our local OCPP 2.0.1 server."""
    print("\n  Redirecting vSECC slot 1 → local OCPP 2.0.1 server")
    ok = True

    v = api.get_var(VAR_SLOT1_URL)
    if not api.set_var(VAR_SLOT1_URL, v, SERVER_URL):
        print("  ERROR: could not set slot 1 URL"); ok = False
    else:
        print(f"  slot_1_url      = {SERVER_URL}")

    v = api.get_var(VAR_SLOT1_VERSION)
    if not api.set_var(VAR_SLOT1_VERSION, v, "OCPP20"):
        print("  ERROR: could not set slot 1 version"); ok = False
    else:
        print("  slot_1_version  = OCPP20")

    v = api.get_var(VAR_SLOT1_SECPROF)
    if not api.set_var(VAR_SLOT1_SECPROF, v, "0"):
        print("  WARN: could not set slot 1 security_profile")
    else:
        print("  slot_1_secprof  = 0 (plain WebSocket, no TLS)")

    return ok


def restore_slot1(api: VseccApi):
    """Restore slot 1 to the original MEA CSMS OCPP 1.6 config."""
    print("\n  Restoring vSECC slot 1 → MEA CSMS OCPP 1.6")

    v = api.get_var(VAR_SLOT1_URL)
    api.set_var(VAR_SLOT1_URL, v, ORIG_SLOT1_URL)
    print(f"  slot_1_url      = {ORIG_SLOT1_URL}")

    v = api.get_var(VAR_SLOT1_VERSION)
    api.set_var(VAR_SLOT1_VERSION, v, ORIG_SLOT1_VERSION)
    print(f"  slot_1_version  = {ORIG_SLOT1_VERSION}")

    v = api.get_var(VAR_SLOT1_SECPROF)
    api.set_var(VAR_SLOT1_SECPROF, v, ORIG_SLOT1_SECPROF)
    print(f"  slot_1_secprof  = {ORIG_SLOT1_SECPROF}")


# ── Summary + save ────────────────────────────────────────────────────────────
def _print_summary():
    print(f"\n{'═'*66}")
    counts = {s: sum(1 for r in results if r["status"] == s)
              for s in ("PASS", "FAIL", "WARN", "INFO")}
    print(f"  OCPP 2.0.1 V2G test  |  "
          f"{counts['PASS']} PASS  {counts['FAIL']} FAIL  {counts['WARN']} WARN")
    print(f"{'═'*66}")
    for r in results:
        tag = {"PASS": "[PASS]", "FAIL": "[FAIL]", "WARN": "[WARN]", "INFO": "[INFO]"}[r["status"]]
        print(f"  {tag} {r['item']:<12}  {r['message'][:55]}")
        if r["detail"]:
            print(f"              {r['detail']}")
    print(f"{'═'*66}")
    out = {
        "test": "ocpp201_v2g",
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "local_csms": SERVER_URL,
        "cp_id": CP_ID,
        "results": results,
    }
    path = "tex/vsecc_ocpp201_v2g_results.json"
    with open(path, "w") as f:
        json.dump(out, f, indent=2)
    print(f"\n  Saved {path}")


# ── Main ──────────────────────────────────────────────────────────────────────
async def _async_main(timeout: int = 90):
    global _charge_point

    async with websockets.serve(
        _csms_handler,
        "0.0.0.0",
        LOCAL_PORT,
        subprotocols=["ocpp2.0.1"],
    ):
        log.info(f"OCPP 2.0.1 CSMS listening on ws://0.0.0.0:{LOCAL_PORT}")

        deadline = time.time() + timeout
        while _charge_point is None and time.time() < deadline:
            await asyncio.sleep(0.5)

        if _charge_point is None:
            record("connect", "vSECC WebSocket connection", "FAIL",
                   f"No connection in {timeout} s — check vSECC slot config")
            return

        record("connect", "vSECC WebSocket connection (OCPP 2.0.1)", "PASS",
               f"Connected from {SERVER_URL}")

        await _run_test_sequence(_charge_point)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--restore", action="store_true",
                        help="Only restore vSECC slot 1 to MEA config")
    parser.add_argument("--server-only", action="store_true",
                        help="Run CSMS server only — configure vSECC manually via web GUI")
    args = parser.parse_args()

    # ── Server-only mode: just run the CSMS, no vSECC config changes ─────────
    if args.server_only:
        print(f"\n{'═'*66}")
        print(f"  OCPP 2.0.1 CSMS ready — configure vSECC manually")
        print(f"  Local CSMS URL : ws://{LOCAL_IP}:{LOCAL_PORT}")
        print(f"  OCPP Version   : OCPP 2.0.1")
        print(f"  Security Profile: 0 (plain WebSocket, no TLS)")
        print(f"  CP: {CP_ID}  |  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'─'*66}")
        print(f"  Set vSECC slot 1 via web GUI to the URL above, save and restart.")
        print(f"  Waiting for vSECC to connect (Ctrl-C to abort)...")
        print(f"{'═'*66}\n")
        asyncio.run(_async_main(timeout=300))
        _print_summary()
        return

    api = VseccApi()
    if not api.login():
        print("ABORT: Cannot reach vSECC")
        sys.exit(1)
    print("  vSECC authenticated")

    # ── Restore mode ──────────────────────────────────────────────────────────
    if args.restore:
        restore_slot1(api)
        print("  Restarting vSECC...")
        api.restart()
        print("  Done — vSECC will reconnect to MEA CSMS via slot 1")
        return

    # ── Auto mode: configure slot 1, restart, test, restore ──────────────────
    if not configure_slot1_for_test(api):
        sys.exit(1)

    print("\n  Restarting vSECC (will connect to local OCPP 2.0.1 server)...")
    api.restart()
    time.sleep(3)

    print(f"\n{'═'*66}")
    print(f"  OCPP 2.0.1 V2G SetChargingProfile test")
    print(f"  CP: {CP_ID}  |  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Local CSMS: ws://{LOCAL_IP}:{LOCAL_PORT}")
    print(f"{'═'*66}")

    try:
        asyncio.run(_async_main(timeout=90))
    finally:
        if not api.login():
            print("\nWARN: Could not re-login. Run: python3 tests/vsecc/test_ocpp201_v2g.py --restore")
        else:
            restore_slot1(api)
            print("\n  Restarting vSECC → will reconnect to MEA CSMS")
            api.restart()

    _print_summary()


if __name__ == "__main__":
    main()

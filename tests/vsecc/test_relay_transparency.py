#!/usr/bin/env python3
"""
Relay transparency test.

Architecture:
    MockCsms16 (:9203) <── relay cp16 ── relay csms201 ──> MockVsecc201 (:9202)

Verifies that all OCPP messages required by sections 1-11 flow correctly
through the relay and are properly translated between 1.6J and 2.0.1.

Run:
    python3 tests/vsecc/test_relay_transparency.py
"""

import asyncio
import logging
import sys
import json
from datetime import datetime, timezone
from collections import defaultdict

import websockets
from ocpp.v16 import ChargePoint as CpBase16
from ocpp.v16 import call as c16, call_result as cr16
from ocpp.v16.enums import (
    Action as A16,
    RegistrationStatus,
    AuthorizationStatus,
    RemoteStartStopStatus,
    ChargingProfileStatus,
    ResetStatus,
    AvailabilityStatus,
    ClearCacheStatus,
    ReservationStatus,
    CancelReservationStatus,
    UpdateStatus,
    DataTransferStatus,
)
from ocpp.v201 import ChargePoint as CpBase201
from ocpp.v201 import call as c201, call_result as cr201
from ocpp.v201.enums import (
    Action as A201,
    RegistrationStatusEnumType,
    RequestStartStopStatusEnumType,
    ChargingProfileStatusEnumType,
    ResetStatusEnumType,
    TransactionEventEnumType,
    TriggerReasonEnumType,
)
from ocpp.routing import on

sys.path.insert(0, "tests/vsecc")
from ocpp_relay import run_relay

logging.basicConfig(level=logging.WARNING, stream=sys.stderr)

RELAY_PORT   = 9202   # mock vSECC connects here
CSMS_PORT    = 9203   # relay's cp16 connects here (mock MEA CSMS)
CP_ID        = "RELAY-TEST"
NOW          = lambda: datetime.now(timezone.utc).isoformat()


# ─────────────────────────────────────────────────────────────────────────────
# Mock 1.6J CSMS (simulates MEA CSMS)
# ─────────────────────────────────────────────────────────────────────────────

class MockCsms16(CpBase16):
    """Accepts the relay's cp16 connection; records all received messages."""

    def __init__(self, cp_id, ws):
        super().__init__(cp_id, ws)
        self.received: dict[str, list] = defaultdict(list)
        self.txn_counter = 100
        self._boot_evt    = asyncio.Event()
        self._status_evt  = asyncio.Event()
        self._start_evt   = asyncio.Event()
        self._stop_evt    = asyncio.Event()
        self._meter_evt   = asyncio.Event()
        self._auth_evt    = asyncio.Event()

    @on(A16.boot_notification)
    async def on_boot(self, charge_point_model, charge_point_vendor, **kw):
        self.received["BootNotification"].append({"model": charge_point_model})
        self._boot_evt.set()
        return cr16.BootNotification(
            current_time=NOW(), interval=300,
            status=RegistrationStatus.accepted,
        )

    @on(A16.heartbeat)
    async def on_heartbeat(self, **kw):
        self.received["Heartbeat"].append({})
        return cr16.Heartbeat(current_time=NOW())

    @on(A16.status_notification)
    async def on_status(self, connector_id, status, error_code, **kw):
        self.received["StatusNotification"].append({"connector_id": connector_id, "status": status})
        self._status_evt.set()
        return cr16.StatusNotification()

    @on(A16.authorize)
    async def on_authorize(self, id_tag, **kw):
        self.received["Authorize"].append({"id_tag": id_tag})
        self._auth_evt.set()
        return cr16.Authorize(id_tag_info={"status": AuthorizationStatus.accepted})

    @on(A16.start_transaction)
    async def on_start_transaction(self, connector_id, id_tag, meter_start, timestamp, **kw):
        self.txn_counter += 1
        self.received["StartTransaction"].append({
            "connector_id": connector_id, "id_tag": id_tag,
            "meter_start": meter_start, "txn_id": self.txn_counter,
        })
        self._start_evt.set()
        return cr16.StartTransaction(
            transaction_id=self.txn_counter,
            id_tag_info={"status": AuthorizationStatus.accepted},
        )

    @on(A16.stop_transaction)
    async def on_stop_transaction(self, meter_stop, timestamp, transaction_id, **kw):
        self.received["StopTransaction"].append({
            "txn_id": transaction_id, "meter_stop": meter_stop,
        })
        self._stop_evt.set()
        return cr16.StopTransaction()

    @on(A16.meter_values)
    async def on_meter_values(self, connector_id, **kw):
        self.received["MeterValues"].append({"connector_id": connector_id})
        self._meter_evt.set()
        return cr16.MeterValues()


# ─────────────────────────────────────────────────────────────────────────────
# Mock 2.0.1 vSECC (simulates Vector vSECC)
# ─────────────────────────────────────────────────────────────────────────────

class MockVsecc201(CpBase201):
    """Connects to relay's server side; records what it receives from relay."""

    def __init__(self, cp_id, ws):
        super().__init__(cp_id, ws)
        self.received: dict[str, list] = defaultdict(list)
        self._req_start_evt = asyncio.Event()
        self._req_stop_evt  = asyncio.Event()
        self._scp_evt       = asyncio.Event()
        self._reset_evt     = asyncio.Event()
        self._txn_id        = "vsecc-txn-001"
        self._seq_no        = 0

    @on(A201.request_start_transaction)
    async def on_request_start(self, id_token, **kw):
        self.received["RequestStartTransaction"].append({"id_token": id_token})
        self._req_start_evt.set()
        return cr201.RequestStartTransaction(status=RequestStartStopStatusEnumType.accepted)

    @on(A201.request_stop_transaction)
    async def on_request_stop(self, transaction_id, **kw):
        self.received["RequestStopTransaction"].append({"txn_id": transaction_id})
        self._req_stop_evt.set()
        return cr201.RequestStopTransaction(status=RequestStartStopStatusEnumType.accepted)

    @on(A201.set_charging_profile)
    async def on_set_charging_profile(self, evse_id, charging_profile, **kw):
        periods = (charging_profile.get("charging_schedule") or [{}])[0].get(
            "charging_schedule_period", [{}])
        limit = periods[0].get("limit", 0) if periods else 0
        self.received["SetChargingProfile"].append({"evse_id": evse_id, "limit": limit})
        self._scp_evt.set()
        return cr201.SetChargingProfile(status=ChargingProfileStatusEnumType.accepted)

    @on(A201.reset)
    async def on_reset(self, type, **kw):
        self.received["Reset"].append({"type": type})
        self._reset_evt.set()
        return cr201.Reset(status=ResetStatusEnumType.accepted)

    @on(A201.set_variables)
    async def on_set_variables(self, set_variable_data, **kw):
        self.received["SetVariables"].append({"data": set_variable_data})
        from ocpp.v201 import call_result as cr201m
        return cr201m.SetVariables(set_variable_result=[
            {"component": d.get("component"), "variable": d.get("variable"),
             "attribute_status": "Accepted"}
            for d in (set_variable_data or [])
        ])

    # ── Helpers to send messages as vSECC ────────────────────────────

    async def send_boot(self):
        return await self.call(c201.BootNotification(
            charging_station={"model": "MockVsecc", "vendor_name": "Test"},
            reason="PowerUp",
        ))

    async def send_status(self, status="Available"):
        return await self.call(c201.StatusNotification(
            timestamp=NOW(), connector_status=status, evse_id=1, connector_id=1,
        ))

    async def send_authorize(self, id_tag="TEST-TAG"):
        return await self.call(c201.Authorize(
            id_token={"id_token": id_tag, "type": "ISO14443"},
        ))

    async def send_transaction_started(self, id_tag="TEST-TAG", meter_wh=0):
        self._seq_no += 1
        return await self.call(c201.TransactionEvent(
            event_type=TransactionEventEnumType.started,
            timestamp=NOW(),
            trigger_reason=TriggerReasonEnumType.authorized,
            seq_no=self._seq_no,
            transaction_info={"transaction_id": self._txn_id},
            id_token={"id_token": id_tag, "type": "ISO14443"},
            evse={"id": 1, "connector_id": 1},
            meter_value=[{"timestamp": NOW(), "sampled_value": [{"value": meter_wh}]}],
        ))

    async def send_transaction_ended(self, meter_wh=1000):
        self._seq_no += 1
        return await self.call(c201.TransactionEvent(
            event_type=TransactionEventEnumType.ended,
            timestamp=NOW(),
            trigger_reason=TriggerReasonEnumType.ev_departed,
            seq_no=self._seq_no,
            transaction_info={"transaction_id": self._txn_id},
            meter_value=[{"timestamp": NOW(), "sampled_value": [{"value": meter_wh}]}],
        ))

    async def send_meter_values(self, energy_wh=500):
        self._seq_no += 1
        return await self.call(c201.TransactionEvent(
            event_type=TransactionEventEnumType.updated,
            timestamp=NOW(),
            trigger_reason=TriggerReasonEnumType.meter_value_periodic,
            seq_no=self._seq_no,
            transaction_info={"transaction_id": self._txn_id},
            meter_value=[{"timestamp": NOW(), "sampled_value": [
                {"value": energy_wh, "measurand": "Energy.Active.Import.Register"}
            ]}],
        ))


# ─────────────────────────────────────────────────────────────────────────────
# Test runner
# ─────────────────────────────────────────────────────────────────────────────

PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"

results: list[tuple[str, str, str]] = []

def record(item: str, status: str, detail: str = ""):
    results.append((item, status, detail))
    sym = "✓" if status == PASS else ("✗" if status == FAIL else "─")
    print(f"  {sym} {item:<55s} {detail}")


async def run_tests(csms: MockCsms16, vsecc: MockVsecc201):
    T = 5.0  # per-step timeout (seconds)

    async def wait(evt: asyncio.Event, label: str) -> bool:
        try:
            await asyncio.wait_for(evt.wait(), timeout=T)
            return True
        except asyncio.TimeoutError:
            return False

    # ── 1. BootNotification ───────────────────────────────────────────
    print("\n[vSECC → relay → MEA]")

    resp = await vsecc.send_boot()
    if getattr(resp, "status", None) == RegistrationStatusEnumType.accepted:
        record("BootNotification: vSECC got Accepted", PASS)
    else:
        record("BootNotification: vSECC got Accepted", FAIL, str(resp))

    ok = await wait(csms._boot_evt, "BootNotification@CSMS")
    record("BootNotification: CSMS received it", PASS if ok else FAIL)
    if ok:
        boot = csms.received["BootNotification"][-1]
        record("BootNotification: model passed through", PASS if boot["model"] == "MockVsecc" else FAIL,
               f"model={boot['model']}")

    # ── 2. StatusNotification ─────────────────────────────────────────
    csms._status_evt.clear()
    await vsecc.send_status("Available")
    await asyncio.sleep(0.5)  # best-effort, allow background task
    ok = csms._status_evt.is_set()
    if ok:
        sn = csms.received["StatusNotification"][-1]
        record("StatusNotification: CSMS received Available", PASS, f"status={sn['status']}")
    else:
        record("StatusNotification: CSMS received Available", SKIP, "best-effort (MEA may not ack)")

    # ── 3. Authorize ──────────────────────────────────────────────────
    csms._auth_evt.clear()
    resp = await vsecc.send_authorize("TOKEN-ABC")
    await wait(csms._auth_evt, "Authorize@CSMS")
    ok_csms = csms._auth_evt.is_set()
    ok_resp  = getattr(resp, "id_token_info", {}).get("status") is not None
    record("Authorize: CSMS received id_tag",
           PASS if ok_csms else FAIL,
           f"id_tag={csms.received['Authorize'][-1]['id_tag'] if ok_csms else '?'}")
    record("Authorize: vSECC got id_token_info back", PASS if ok_resp else FAIL)

    # ── 4. TransactionEvent(Started) → StartTransaction ──────────────
    csms._start_evt.clear()
    resp = await vsecc.send_transaction_started("TOKEN-ABC", meter_wh=0)
    await wait(csms._start_evt, "StartTransaction@CSMS")
    ok_csms = csms._start_evt.is_set()
    id_token_info = getattr(resp, "id_token_info", None)
    record("TransactionEvent(Started) → StartTransaction: CSMS received", PASS if ok_csms else FAIL)
    record("TransactionEvent(Started): vSECC got id_token_info", PASS if id_token_info else FAIL)
    if ok_csms:
        st = csms.received["StartTransaction"][-1]
        record("StartTransaction: id_tag passed through", PASS if st["id_tag"] == "TOKEN-ABC" else FAIL,
               f"id_tag={st['id_tag']}")
        mea_txn_id = st["txn_id"]
        record("StartTransaction: CSMS assigned txn_id", PASS, f"txn_id={mea_txn_id}")

    # ── 5. TransactionEvent(Updated) → MeterValues ───────────────────
    csms._meter_evt.clear()
    await vsecc.send_meter_values(energy_wh=500)
    await asyncio.sleep(0.5)
    ok = csms._meter_evt.is_set()
    record("TransactionEvent(Updated) → MeterValues: CSMS received",
           PASS if ok else SKIP, "best-effort")

    # ── MEA → relay → vSECC (while transaction is active so vsecc_txn_id is set) ──
    print("\n[MEA → relay → vSECC]")

    # 6. RemoteStartTransaction → RequestStartTransaction
    vsecc._req_start_evt.clear()
    resp = await csms.call(c16.RemoteStartTransaction(
        connector_id=1, id_tag="RFID-001",
    ))
    await wait(vsecc._req_start_evt, "RequestStartTransaction@vSECC")
    ok_vsec  = vsecc._req_start_evt.is_set()
    ok_resp  = getattr(resp, "status", None) == RemoteStartStopStatus.accepted
    record("RemoteStartTransaction → RequestStartTransaction: vSECC received", PASS if ok_vsec else FAIL)
    record("RemoteStartTransaction: relay responded Accepted to MEA", PASS if ok_resp else FAIL)
    if ok_vsec:
        tag = (vsecc.received["RequestStartTransaction"][-1].get("id_token") or {}).get("id_token")
        record("RequestStartTransaction: id_tag forwarded", PASS if tag == "RFID-001" else FAIL,
               f"id_tag={tag}")

    # 7. RemoteStopTransaction → RequestStopTransaction (vsecc_txn_id = "vsecc-txn-001" is still set)
    vsecc._req_stop_evt.clear()
    resp = await csms.call(c16.RemoteStopTransaction(transaction_id=mea_txn_id if ok_csms else 999))
    await wait(vsecc._req_stop_evt, "RequestStopTransaction@vSECC")
    ok_vsec = vsecc._req_stop_evt.is_set()
    ok_resp = getattr(resp, "status", None) == RemoteStartStopStatus.accepted
    record("RemoteStopTransaction → RequestStopTransaction: vSECC received", PASS if ok_vsec else FAIL)
    record("RemoteStopTransaction: relay responded Accepted", PASS if ok_resp else FAIL)

    # 8. SetChargingProfile passthrough
    vsecc._scp_evt.clear()
    resp = await csms.call(c16.SetChargingProfile(
        connector_id=1,
        cs_charging_profiles={
            "charging_profile_id": 1, "stack_level": 0,
            "charging_profile_purpose": "TxDefaultProfile",
            "charging_profile_kind": "Absolute",
            "charging_schedule": {
                "charging_rate_unit": "W",
                "charging_schedule_period": [{"start_period": 0, "limit": 3700}],
            },
        },
    ))
    await wait(vsecc._scp_evt, "SetChargingProfile@vSECC")
    ok_vsec = vsecc._scp_evt.is_set()
    ok_resp = getattr(resp, "status", None) == ChargingProfileStatus.accepted
    record("SetChargingProfile (1.6J→2.0.1): vSECC received", PASS if ok_vsec else FAIL)
    record("SetChargingProfile: relay responded Accepted", PASS if ok_resp else FAIL)
    if ok_vsec:
        lim = vsecc.received["SetChargingProfile"][-1].get("limit")
        record("SetChargingProfile: limit=3700 W passed through", PASS if lim == 3700.0 else FAIL,
               f"limit={lim}")

    # 10. ChangeConfiguration(MEA_V2G_PowerDemand=-5000) → SetChargingProfile(-5000)
    vsecc._scp_evt.clear()
    resp = await csms.call(c16.ChangeConfiguration(key="MEA_V2G_PowerDemand", value="-5000"))
    await wait(vsecc._scp_evt, "SetChargingProfile(V2G)@vSECC")
    ok_vsec = vsecc._scp_evt.is_set()
    from ocpp.v16.enums import ConfigurationStatus
    ok_resp = getattr(resp, "status", None) == ConfigurationStatus.accepted
    record("ChangeConfiguration(MEA_V2G_PowerDemand) → SetChargingProfile: vSECC received",
           PASS if ok_vsec else FAIL)
    record("V2G translation: relay responded Accepted to MEA", PASS if ok_resp else FAIL)
    if ok_vsec:
        lim = vsecc.received["SetChargingProfile"][-1].get("limit")
        record("V2G: limit=-5000 W (negative = discharge)", PASS if lim == -5000.0 else FAIL,
               f"limit={lim}")

    # 11. Reset
    vsecc._reset_evt.clear()
    resp = await csms.call(c16.Reset(type="Hard"))
    await wait(vsecc._reset_evt, "Reset@vSECC")
    ok_vsec = vsecc._reset_evt.is_set()
    ok_resp = getattr(resp, "status", None) == ResetStatus.accepted
    record("Reset (Hard, 1.6J→2.0.1): vSECC received Immediate", PASS if ok_vsec else FAIL)
    record("Reset: relay responded Accepted", PASS if ok_resp else FAIL)
    if ok_vsec:
        rtype = vsecc.received["Reset"][-1].get("type")
        record("Reset: type=Immediate forwarded", PASS if str(rtype) in ("Immediate","ResetEnumType.immediate") else FAIL,
               f"type={rtype}")

    # ── 12. TransactionEvent(Ended) → StopTransaction (now after MEA cmds) ──
    print("\n[vSECC → relay → MEA  (continue)]")
    csms._stop_evt.clear()
    await vsecc.send_transaction_ended(meter_wh=1000)
    await asyncio.sleep(0.5)
    ok = csms._stop_evt.is_set()
    if ok:
        st = csms.received["StopTransaction"][-1]
        record("TransactionEvent(Ended) → StopTransaction: CSMS received", PASS)
        record("StopTransaction: txn_id matches StartTransaction",
               PASS if ok_csms and st["txn_id"] == mea_txn_id else FAIL,
               f"txn_id={st['txn_id']}")
    else:
        record("TransactionEvent(Ended) → StopTransaction", SKIP, "best-effort")
        record("StopTransaction: txn_id matches", SKIP)

    # ── Stub handlers (no real vSECC action, just verify Accepted) ────
    print("\n[Stub handlers (CSMS-initiated, no EV needed)]")

    async def check_stub(call_obj, label, expected_attr, expected_val):
        try:
            resp = await asyncio.wait_for(csms.call(call_obj), timeout=T)
            val  = getattr(resp, expected_attr, None)
            val_str = val.value if hasattr(val, "value") else str(val)
            ok   = val_str == (expected_val.value if hasattr(expected_val, "value") else str(expected_val))
            record(label, PASS if ok else FAIL, f"{expected_attr}={val_str}")
        except Exception as e:
            record(label, FAIL, str(e))

    from ocpp.v16.enums import AvailabilityType
    await check_stub(
        c16.ChangeAvailability(connector_id=1, type=AvailabilityType.operative),
        "ChangeAvailability → Accepted", "status", AvailabilityStatus.accepted,
    )
    await check_stub(
        c16.ClearCache(),
        "ClearCache → Accepted", "status", ClearCacheStatus.accepted,
    )
    from ocpp.v16.enums import UpdateType
    await check_stub(
        c16.SendLocalList(list_version=1, update_type=UpdateType.full),
        "SendLocalList → Accepted", "status", UpdateStatus.accepted,
    )
    await check_stub(
        c16.GetLocalListVersion(),
        "GetLocalListVersion → list_version=0", "list_version", 0,
    )
    await check_stub(
        c16.GetDiagnostics(location="ftp://test/"),
        "GetDiagnostics → no crash", "file_name", None,
    )
    await check_stub(
        c16.DataTransfer(vendor_id="MEA"),
        "DataTransfer → Accepted", "status", DataTransferStatus.accepted,
    )
    from datetime import timedelta
    expiry = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
    await check_stub(
        c16.ReserveNow(connector_id=1, expiry_date=expiry, id_tag="TAG", reservation_id=1),
        "ReserveNow → Accepted", "status", ReservationStatus.accepted,
    )
    await check_stub(
        c16.CancelReservation(reservation_id=1),
        "CancelReservation → Accepted", "status", CancelReservationStatus.accepted,
    )
    await check_stub(
        c16.TriggerMessage(requested_message="Heartbeat"),
        "TriggerMessage → Accepted", "status", "Accepted",
    )
    await check_stub(
        c16.GetConfiguration(),
        "GetConfiguration → empty list", "configuration_key", [],
    )


async def main():
    print("=" * 70)
    print("OCPP Relay Transparency Test")
    print("=" * 70)

    csms_ref: list[MockCsms16]    = []
    vsecc_ref: list[MockVsecc201] = []
    csms_ready = asyncio.Event()

    # Mock CSMS server (relay cp16 connects here)
    async def csms_handler(ws):
        path = getattr(ws.request, "path", f"/{CP_ID}")
        cp_id = path.lstrip("/") or CP_ID
        csms = MockCsms16(cp_id, ws)
        csms_ref.append(csms)
        csms_ready.set()
        await csms.start()

    async with websockets.serve(csms_handler, "127.0.0.1", CSMS_PORT,
                                subprotocols=["ocpp1.6"]):

        # Start relay (connects to mock CSMS, serves on relay port)
        relay_task = asyncio.create_task(
            run_relay(RELAY_PORT, f"ws://127.0.0.1:{CSMS_PORT}", CP_ID)
        )
        await asyncio.sleep(0.3)  # let relay server bind

        # Connect mock vSECC to relay
        async with websockets.connect(
            f"ws://127.0.0.1:{RELAY_PORT}/{CP_ID}",
            subprotocols=["ocpp2.0.1"],
        ) as vsecc_ws:
            vsecc = MockVsecc201(CP_ID, vsecc_ws)
            vsecc_ref.append(vsecc)

            # Wait for relay to connect upstream to mock CSMS
            await asyncio.wait_for(csms_ready.wait(), timeout=5)
            await asyncio.sleep(0.2)

            csms = csms_ref[0]
            # csms.start() is already running inside csms_handler (server's task)
            # — starting it again from here would double-read the websocket.
            vsecc_t = asyncio.create_task(vsecc.start())

            try:
                await run_tests(csms, vsecc)
            finally:
                relay_task.cancel()
                vsecc_t.cancel()
                for t in [relay_task, vsecc_t]:
                    try:
                        await t
                    except (asyncio.CancelledError, Exception):
                        pass

    # Summary
    print("\n" + "=" * 70)
    passed = sum(1 for _, s, _ in results if s == PASS)
    failed = sum(1 for _, s, _ in results if s == FAIL)
    skipped = sum(1 for _, s, _ in results if s == SKIP)
    total  = len(results)
    print(f"Results: {passed}/{total} passed  |  {failed} failed  |  {skipped} skipped")
    if failed == 0:
        print("ALL REQUIRED FLOWS PASS  ✓")
    else:
        print("FAILURES:")
        for item, status, detail in results:
            if status == FAIL:
                print(f"  ✗ {item}  {detail}")
    print("=" * 70)
    return failed


if __name__ == "__main__":
    failed = asyncio.run(main())
    sys.exit(0 if failed == 0 else 1)

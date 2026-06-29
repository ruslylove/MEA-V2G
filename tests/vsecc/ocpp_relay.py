#!/usr/bin/env python3
"""
OCPP Relay: MEA CSMS (OCPP 1.6J) <-> LOCAL RELAY <-> vSECC (OCPP 2.0.1)

The relay sits between the MEA CSMS and the vSECC, translating messages
between OCPP versions so the MEA 1.6J CSMS can control the vSECC which
only speaks OCPP 2.0.1.

Key V2G translation (the main reason this relay exists):
  MEA  ChangeConfiguration(MEA_V2G_PowerDemand=-5000)
  →  vSECC  SetChargingProfile(limit=-5000 W, TxProfile)

Message map:
  vSECC → relay → MEA
    BootNotification (2.0.1)          → BootNotification (1.6J)
    Heartbeat                         → Heartbeat
    StatusNotification                → StatusNotification
    Authorize                         → Authorize
    TransactionEvent(Started)         → StartTransaction
    TransactionEvent(Ended)           → StopTransaction
    TransactionEvent(Updated+meters)  → MeterValues

  MEA → relay → vSECC
    RemoteStartTransaction (1.6J)     → RequestStartTransaction (2.0.1)
    RemoteStopTransaction             → RequestStopTransaction
    SetChargingProfile                → SetChargingProfile (limit may be negative)
    ChangeConfiguration(Power.Active.Import) → SetChargingProfile(limit=value W)
    ChangeConfiguration(other)        → SetVariables
    Reset                             → Reset

Usage:
  # 1. Start the relay (on the same host reachable by vSECC)
  python3 tests/vsecc/ocpp_relay.py --port 9201 --cp-id rddQC4000001

  # 2. Configure vSECC slot 1 to point here, then enable it:
  #    network_configuration_slot_1_ocpp_csms_url = ws://192.168.1.200:9201
  #    network_configuration_priority = ["1"]
  #    (restart vSECC)

  # 3. Relay will forward all traffic to MEA CSMS transparently
"""

import asyncio
import argparse
import logging
import sys
from datetime import datetime, timezone

import websockets
from ocpp.v16 import ChargePoint as CpBase16
from ocpp.v16 import call as c16, call_result as cr16
from ocpp.v16.enums import (
    Action as A16,
    AuthorizationStatus,
    ChargePointErrorCode,
    ChargePointStatus,
    ChargingProfileStatus,
    ConfigurationStatus,
    RemoteStartStopStatus,
    ResetStatus,
    TriggerMessageStatus,
    UnlockStatus,
)
from ocpp.v201 import ChargePoint as CpBase201
from ocpp.v201 import call as c201, call_result as cr201
from ocpp.v201.enums import (
    Action as A201,
    AuthorizationStatusEnumType,
    ChargingProfileKindEnumType,
    ChargingProfilePurposeEnumType,
    ChargingProfileStatusEnumType,
    ChargingRateUnitEnumType,
    ConnectorStatusEnumType,
    RegistrationStatusEnumType,
    RequestStartStopStatusEnumType,
    ResetEnumType,
    ResetStatusEnumType,
    TransactionEventEnumType,
)
from ocpp.routing import on

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    stream=sys.stdout,
    force=True,
)
log = logging.getLogger("relay")


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _extract_energy_wh(meter_values: list) -> float:
    """Pull the first numeric reading from a 2.0.1 meter_value list (Wh)."""
    for mv in meter_values or []:
        for sv in mv.get("sampled_value", []):
            try:
                return float(sv.get("value", 0))
            except (TypeError, ValueError):
                pass
    return 0.0


def _to_meter_values_16(meter_values: list) -> list:
    """Convert OCPP 2.0.1 meter_value list to 1.6J MeterValues format."""
    result = []
    for mv in meter_values or []:
        sampled = []
        for sv in mv.get("sampled_value", []):
            uom = sv.get("unit_of_measure") or {}
            unit = uom.get("unit", "Wh") if isinstance(uom, dict) else "Wh"
            sampled.append({
                "value": str(sv.get("value", "0")),
                "measurand": sv.get("measurand", "Energy.Active.Import.Register"),
                "unit": unit,
            })
        if sampled:
            result.append({"timestamp": mv.get("timestamp", _now()), "sampled_value": sampled})
    return result


def _auth_status_to_201(raw) -> AuthorizationStatusEnumType:
    """Map 1.6J AuthorizationStatus (enum or string) → 2.0.1 equivalent."""
    s = raw.value if hasattr(raw, "value") else str(raw)
    return {
        "Accepted": AuthorizationStatusEnumType.accepted,
        "Blocked":  AuthorizationStatusEnumType.blocked,
        "Expired":  AuthorizationStatusEnumType.expired,
        "Invalid":  AuthorizationStatusEnumType.unknown,
    }.get(s, AuthorizationStatusEnumType.unknown)


# ─────────────────────────────────────────────────────────────────────────────
# Shared relay session state
# ─────────────────────────────────────────────────────────────────────────────

class RelaySession:
    """Mutable state shared between both sides of the relay."""

    def __init__(self):
        self.csms: "RelayCsms201 | None" = None  # vSECC side
        self.cp:   "RelayCp16   | None" = None   # MEA side
        self.vsecc_txn_id: str | None = None
        self.mea_txn_id:   int | None = None
        self.id_tag: str = "RELAY"
        self.connector_id: int = 1


# ─────────────────────────────────────────────────────────────────────────────
# Side A — accepts vSECC (relay acts as OCPP 2.0.1 CSMS)
# ─────────────────────────────────────────────────────────────────────────────

class RelayCsms201(CpBase201):
    """Handles incoming vSECC connection; relay presents itself as a 2.0.1 CSMS."""

    def __init__(self, cp_id: str, ws, session: RelaySession):
        super().__init__(cp_id, ws)
        self.s = session
        session.csms = self

    # ── Forwarded vSECC → MEA ────────────────────────────────────────

    @on(A201.boot_notification)
    async def on_boot(self, charging_station, reason, **kw):
        model  = (charging_station or {}).get("model", "vSECC")
        vendor = (charging_station or {}).get("vendor_name", "Vector")
        log.info(f"[vSECC→] BootNotification  model={model}  reason={reason}")
        if self.s.cp:
            resp = await self.s.cp.call(c16.BootNotification(
                charge_point_model=model, charge_point_vendor=vendor,
            ))
            log.info(f"[→MEA]  BootNotification  status={resp.status}")
        return cr201.BootNotification(
            current_time=_now(), interval=300,
            status=RegistrationStatusEnumType.accepted,
        )

    @on(A201.heartbeat)
    async def on_heartbeat(self, **kw):
        if self.s.cp:
            asyncio.create_task(self._best_effort(self.s.cp.call(c16.Heartbeat())))
        return cr201.Heartbeat(current_time=_now())

    @on(A201.status_notification)
    async def on_status(self, timestamp, connector_status, evse_id, connector_id, **kw):
        log.info(f"[vSECC→] StatusNotification  evse={evse_id}  status={connector_status}")
        status_map = {
            ConnectorStatusEnumType.available:   ChargePointStatus.available,
            ConnectorStatusEnumType.occupied:    ChargePointStatus.charging,
            ConnectorStatusEnumType.reserved:    ChargePointStatus.reserved,
            ConnectorStatusEnumType.unavailable: ChargePointStatus.unavailable,
            ConnectorStatusEnumType.faulted:     ChargePointStatus.faulted,
        }
        # Map by value string in case library passes enum or raw string
        s16 = ChargePointStatus.available
        cs_str = connector_status.value if hasattr(connector_status, "value") else str(connector_status)
        for enum_key, v16 in status_map.items():
            if (enum_key.value if hasattr(enum_key, "value") else str(enum_key)) == cs_str:
                s16 = v16
                break
        if self.s.cp:
            asyncio.create_task(self._best_effort(self.s.cp.call(c16.StatusNotification(
                connector_id=int(connector_id or evse_id or 1),
                error_code=ChargePointErrorCode.no_error,
                status=s16,
            ))))
        return cr201.StatusNotification()

    @staticmethod
    async def _best_effort(coro, timeout: float = 5.0):
        """Run coro, silently discard TimeoutError or any exception."""
        try:
            await asyncio.wait_for(coro, timeout=timeout)
        except Exception:
            pass

    @on(A201.notify_event)
    async def on_notify_event(self, generated_at, seq_no, event_data, **kw):
        # 2.0.1-only diagnostic event; acknowledge and discard (no 1.6J equivalent)
        log.debug(f"[vSECC→] NotifyEvent  seq={seq_no}  events={len(event_data or [])}")
        return cr201.NotifyEvent()

    @on(A201.security_event_notification)
    async def on_security_event(self, timestamp, type, **kw):
        # 2.0.1-only security log; acknowledge and discard
        log.debug(f"[vSECC→] SecurityEventNotification  type={type}")
        return cr201.SecurityEventNotification()

    @on(A201.authorize)
    async def on_authorize(self, id_token, **kw):
        tag = (id_token or {}).get("id_token", "UNKNOWN") if isinstance(id_token, dict) else str(id_token)
        log.info(f"[vSECC→] Authorize  id_token={tag}")
        status = AuthorizationStatusEnumType.accepted
        if self.s.cp:
            resp = await self.s.cp.call(c16.Authorize(id_tag=tag))
            raw = (resp.id_tag_info or {}).get("status", AuthorizationStatus.accepted)
            status = _auth_status_to_201(raw)
        return cr201.Authorize(id_token_info={"status": status})

    @on(A201.transaction_event)
    async def on_transaction_event(self, event_type, timestamp, trigger_reason,
                                   seq_no, transaction_info, **kw):
        txn_id = (transaction_info or {}).get("transaction_id", "")
        id_token    = kw.get("id_token") or {}
        tag         = id_token.get("id_token", self.s.id_tag) if isinstance(id_token, dict) else self.s.id_tag
        evse        = kw.get("evse") or {}
        connector   = int(evse.get("connector_id") or evse.get("id") or 1)
        meter_values = kw.get("meter_value") or []
        log.info(f"[vSECC→] TransactionEvent  type={event_type}  txn={txn_id}")

        if event_type == TransactionEventEnumType.started:
            self.s.vsecc_txn_id  = txn_id
            self.s.id_tag        = tag
            self.s.connector_id  = connector
            meter_start = int(_extract_energy_wh(meter_values))
            if self.s.cp:
                resp = await self.s.cp.call(c16.StartTransaction(
                    connector_id=connector, id_tag=tag,
                    meter_start=meter_start, timestamp=timestamp,
                ))
                self.s.mea_txn_id = resp.transaction_id
                log.info(f"[→MEA]  StartTransaction  mea_txn_id={resp.transaction_id}")
            return cr201.TransactionEvent(
                id_token_info={"status": AuthorizationStatusEnumType.accepted},
            )

        if event_type == TransactionEventEnumType.ended:
            meter_stop = int(_extract_energy_wh(meter_values))
            if self.s.cp and self.s.mea_txn_id is not None:
                mea_txn = self.s.mea_txn_id
                id_tag  = self.s.id_tag
                log.info(f"[→MEA]  StopTransaction  mea_txn_id={mea_txn}")
                asyncio.create_task(self._best_effort(self.s.cp.call(c16.StopTransaction(
                    meter_stop=meter_stop, timestamp=timestamp,
                    transaction_id=mea_txn, id_tag=id_tag,
                )), timeout=15.0))
            self.s.vsecc_txn_id = None
            self.s.mea_txn_id   = None
            return cr201.TransactionEvent()

        # Updated — fire-and-forget meter values
        if self.s.cp and self.s.mea_txn_id is not None and meter_values:
            mv16 = _to_meter_values_16(meter_values)
            if mv16:
                asyncio.create_task(self._best_effort(self.s.cp.call(c16.MeterValues(
                    connector_id=self.s.connector_id,
                    transaction_id=self.s.mea_txn_id,
                    meter_value=mv16,
                ))))
        return cr201.TransactionEvent()


# ─────────────────────────────────────────────────────────────────────────────
# Side B — connects upstream to MEA CSMS (relay acts as OCPP 1.6J charge point)
# ─────────────────────────────────────────────────────────────────────────────

class RelayCp16(CpBase16):
    """Upstream connection to MEA CSMS; relay presents itself as a 1.6J charge point."""

    def __init__(self, cp_id: str, ws, session: RelaySession):
        super().__init__(cp_id, ws)
        self.s = session
        session.cp = self

    # ── Commands from MEA CSMS → forwarded to vSECC ──────────────────

    @on(A16.remote_start_transaction)
    async def on_remote_start(self, id_tag, **kw):
        connector_id = int(kw.get("connector_id") or 1)
        log.info(f"[MEA→]  RemoteStartTransaction  id_tag={id_tag}  connector={connector_id}")
        if self.s.csms:
            resp = await self.s.csms.call(c201.RequestStartTransaction(
                id_token={"id_token": id_tag, "type": "Central"},
                evse_id=connector_id,
                remote_start_id=1,
            ))
            ok = resp.status == RequestStartStopStatusEnumType.accepted
            log.info(f"[→vSECC] RequestStartTransaction  status={resp.status}")
            return cr16.RemoteStartTransaction(
                status=RemoteStartStopStatus.accepted if ok else RemoteStartStopStatus.rejected,
            )
        return cr16.RemoteStartTransaction(status=RemoteStartStopStatus.rejected)

    @on(A16.remote_stop_transaction)
    async def on_remote_stop(self, transaction_id, **kw):
        log.info(f"[MEA→]  RemoteStopTransaction  mea_txn_id={transaction_id}")
        if self.s.csms and self.s.vsecc_txn_id:
            resp = await self.s.csms.call(c201.RequestStopTransaction(
                transaction_id=self.s.vsecc_txn_id,
            ))
            ok = resp.status == RequestStartStopStatusEnumType.accepted
            log.info(f"[→vSECC] RequestStopTransaction  status={resp.status}")
            return cr16.RemoteStopTransaction(
                status=RemoteStartStopStatus.accepted if ok else RemoteStartStopStatus.rejected,
            )
        return cr16.RemoteStopTransaction(status=RemoteStartStopStatus.rejected)

    @on(A16.set_charging_profile)
    async def on_set_charging_profile(self, connector_id, cs_charging_profiles, **kw):
        log.info(f"[MEA→]  SetChargingProfile  connector={connector_id}")
        if not self.s.csms:
            return cr16.SetChargingProfile(status=ChargingProfileStatus.rejected)
        profile16 = cs_charging_profiles or {}
        schedule16 = profile16.get("charging_schedule") or {}
        periods = [
            {"start_period": p.get("start_period", 0), "limit": float(p.get("limit", 0))}
            for p in (schedule16.get("charging_schedule_period") or [])
        ]
        purpose = (
            ChargingProfilePurposeEnumType.tx_profile
            if self.s.vsecc_txn_id else
            ChargingProfilePurposeEnumType.tx_default_profile
        )
        profile201 = {
            "id": profile16.get("charging_profile_id", 1),
            "stack_level": profile16.get("stack_level", 0),
            "charging_profile_purpose": purpose,
            "charging_profile_kind": ChargingProfileKindEnumType.absolute,
            "charging_schedule": [{
                "id": 1,
                "charging_rate_unit": ChargingRateUnitEnumType.watts,
                "charging_schedule_period": periods,
            }],
        }
        if self.s.vsecc_txn_id:
            profile201["transaction_id"] = self.s.vsecc_txn_id
        resp = await self.s.csms.call(c201.SetChargingProfile(
            evse_id=int(connector_id or 1),
            charging_profile=profile201,
        ))
        ok = resp.status == ChargingProfileStatusEnumType.accepted
        log.info(f"[→vSECC] SetChargingProfile  status={resp.status}")
        return cr16.SetChargingProfile(
            status=ChargingProfileStatus.accepted if ok else ChargingProfileStatus.rejected,
        )

    @on(A16.change_configuration)
    async def on_change_config(self, key, value, **kw):
        log.info(f"[MEA→]  ChangeConfiguration  key={key}  value={value}")
        if key in ("MEA_V2G_PowerDemand", "Power.Active.Import"):
            return await self._apply_power_limit(float(value))
        # Generic key: forward as SetVariables
        if self.s.csms:
            await self.s.csms.call(c201.SetVariables(set_variable_data=[{
                "component": {"name": "CustomConfiguration"},
                "variable":  {"name": key},
                "attribute_value": str(value),
            }]))
        return cr16.ChangeConfiguration(status=ConfigurationStatus.accepted)

    @on(A16.reset)
    async def on_reset(self, type, **kw):
        log.info(f"[MEA→]  Reset  type={type}")
        if self.s.csms:
            rtype = ResetEnumType.immediate if type == "Hard" else ResetEnumType.on_idle
            resp  = await self.s.csms.call(c201.Reset(type=rtype))
            ok    = getattr(resp, "status", None) == ResetStatusEnumType.accepted
            return cr16.Reset(status=ResetStatus.accepted if ok else ResetStatus.rejected)
        return cr16.Reset(status=ResetStatus.rejected)

    @on(A16.get_configuration)
    async def on_get_config(self, key=None, **kw):
        return cr16.GetConfiguration(configuration_key=[], unknown_key=key or [])

    @on(A16.trigger_message)
    async def on_trigger(self, requested_message, **kw):
        return cr16.TriggerMessage(status=TriggerMessageStatus.accepted)

    @on(A16.unlock_connector)
    async def on_unlock(self, connector_id, **kw):
        return cr16.UnlockConnector(status=UnlockStatus.unlocked)

    @on(A16.change_availability)
    async def on_change_availability(self, connector_id, type, **kw):
        log.info(f"[MEA→]  ChangeAvailability  connector={connector_id}  type={type}")
        from ocpp.v16.enums import AvailabilityStatus
        return cr16.ChangeAvailability(status=AvailabilityStatus.accepted)

    @on(A16.clear_cache)
    async def on_clear_cache(self, **kw):
        log.info("[MEA→]  ClearCache")
        from ocpp.v16.enums import ClearCacheStatus
        return cr16.ClearCache(status=ClearCacheStatus.accepted)

    @on(A16.reserve_now)
    async def on_reserve_now(self, connector_id, expiry_date, id_tag, reservation_id, **kw):
        log.info(f"[MEA→]  ReserveNow  connector={connector_id}  id={reservation_id}")
        from ocpp.v16.enums import ReservationStatus
        return cr16.ReserveNow(status=ReservationStatus.accepted)

    @on(A16.cancel_reservation)
    async def on_cancel_reservation(self, reservation_id, **kw):
        log.info(f"[MEA→]  CancelReservation  id={reservation_id}")
        from ocpp.v16.enums import CancelReservationStatus
        return cr16.CancelReservation(status=CancelReservationStatus.accepted)

    @on(A16.send_local_list)
    async def on_send_local_list(self, list_version, update_type, **kw):
        log.info(f"[MEA→]  SendLocalList  version={list_version}  type={update_type}")
        from ocpp.v16.enums import UpdateStatus
        return cr16.SendLocalList(status=UpdateStatus.accepted)

    @on(A16.get_local_list_version)
    async def on_get_local_list_version(self, **kw):
        return cr16.GetLocalListVersion(list_version=0)

    @on(A16.get_diagnostics)
    async def on_get_diagnostics(self, location, **kw):
        log.info(f"[MEA→]  GetDiagnostics  location={location}")
        return cr16.GetDiagnostics(file_name=None)

    @on(A16.update_firmware)
    async def on_update_firmware(self, location, retrieve_date, **kw):
        log.info(f"[MEA→]  UpdateFirmware  location={location}")
        return cr16.UpdateFirmware()

    @on(A16.data_transfer)
    async def on_data_transfer(self, vendor_id, **kw):
        log.info(f"[MEA→]  DataTransfer  vendor={vendor_id}")
        from ocpp.v16.enums import DataTransferStatus
        return cr16.DataTransfer(status=DataTransferStatus.accepted, data=None)

    # ── V2G core: Power.Active.Import → SetChargingProfile(negative) ─

    async def _apply_power_limit(self, power_w: float):
        """
        Translate MEA's proprietary Power.Active.Import key into a native
        OCPP 2.0.1 SetChargingProfile. Negative power_w means V2G discharge.
        """
        if not self.s.csms:
            return cr16.ChangeConfiguration(status=ConfigurationStatus.rejected)

        purpose = (
            ChargingProfilePurposeEnumType.tx_profile
            if self.s.vsecc_txn_id else
            ChargingProfilePurposeEnumType.tx_default_profile
        )
        profile = {
            "id": 99,
            "stack_level": 0,
            "charging_profile_purpose": purpose,
            "charging_profile_kind": ChargingProfileKindEnumType.absolute,
            "charging_schedule": [{
                "id": 99,
                "charging_rate_unit": ChargingRateUnitEnumType.watts,
                "charging_schedule_period": [{"start_period": 0, "limit": power_w}],
            }],
        }
        if self.s.vsecc_txn_id:
            profile["transaction_id"] = self.s.vsecc_txn_id

        direction = "V2G discharge" if power_w < 0 else ("idle/stop" if power_w == 0 else "charge")
        log.info(f"[relay]  MEA_V2G_PowerDemand={power_w} W → SetChargingProfile ({direction})")

        resp = await self.s.csms.call(c201.SetChargingProfile(
            evse_id=1, charging_profile=profile,
        ))
        ok = resp.status == ChargingProfileStatusEnumType.accepted
        log.info(f"[→vSECC] SetChargingProfile({power_w} W)  status={resp.status}")
        return cr16.ChangeConfiguration(
            status=ConfigurationStatus.accepted if ok else ConfigurationStatus.rejected,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Relay runner
# ─────────────────────────────────────────────────────────────────────────────

async def run_relay(local_port: int, mea_url: str, cp_id: str):
    async def handler(websocket):
        path     = getattr(websocket.request, "path", f"/{cp_id}")
        seen_id  = path.lstrip("/") or cp_id
        log.info(f"── vSECC connected  cp_id={seen_id}  subprotocol={websocket.subprotocol}")

        session = RelaySession()
        csms    = RelayCsms201(seen_id, websocket, session)

        mea_full = f"{mea_url}/{seen_id}"
        log.info(f"── Connecting to MEA: {mea_full}")
        try:
            async with websockets.connect(mea_full, subprotocols=["ocpp1.6"]) as mea_ws:
                RelayCp16(seen_id, mea_ws, session)
                log.info("── MEA connected (OCPP 1.6J)")
                t1 = asyncio.create_task(csms.start())
                t2 = asyncio.create_task(session.cp.start())
                done, pending = await asyncio.wait(
                    [t1, t2], return_when=asyncio.FIRST_COMPLETED,
                )
                for t in pending:
                    t.cancel()
                    try:
                        await t
                    except asyncio.CancelledError:
                        pass
        except Exception as e:
            log.error(f"MEA connection error: {e}")
        log.info("── Session ended")

    log.info(f"OCPP Relay  port={local_port}  upstream={mea_url}")
    async with websockets.serve(handler, "0.0.0.0", local_port, subprotocols=["ocpp2.0.1"]):
        log.info("Waiting for vSECC to connect …")
        await asyncio.Future()  # run forever


def main():
    p = argparse.ArgumentParser(
        description="OCPP 1.6J ↔ 2.0.1 Relay — bridges MEA CSMS to vSECC",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--port",    type=int, default=9201,
                   help="Local WebSocket port the vSECC connects to")
    p.add_argument("--mea-url", default="wss://ocpp.measandbox.com:2930/EV/Srv/JSON/1.6",
                   help="MEA CSMS base URL (CP_ID is appended as path component)")
    p.add_argument("--cp-id",   default="rddQC4000001",
                   help="Charge point identity used with MEA CSMS")
    p.add_argument("--debug",   action="store_true",
                   help="Enable DEBUG-level logging")
    args = p.parse_args()
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
    asyncio.run(run_relay(args.port, args.mea_url, args.cp_id))


if __name__ == "__main__":
    main()

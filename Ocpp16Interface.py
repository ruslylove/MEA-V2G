import logging
import asyncio
from ocpp.v16 import ChargePoint as Ocpp16ChargePoint
from ocpp.v16 import call, call_result
from ocpp.v16.enums import (
    Action, 
    RegistrationStatus, 
    AuthorizationStatus, 
    ChargePointStatus, 
    ConfigurationStatus,
    UnlockStatus,
    ResetStatus,
    ReservationStatus,
    CancelReservationStatus,
    ChargingProfileStatus,
    ClearCacheStatus,
    UpdateStatus,
    AvailabilityStatus
)
from ocpp.routing import on
from Charger import Charger

logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger('ocpp_16_interface')

class Ocpp16Interface(Ocpp16ChargePoint):
    def __init__(self, id, connection, charger: Charger):
        super().__init__(id, connection)
        self.charger = charger
        self.transaction_id = None

    async def send_boot_notification(self, model="MEA-V2G-01", vendor="RuslyLove"):
        request = call.BootNotificationPayload(
            charge_point_model=model,
            charge_point_vendor=vendor
        )
        response = await self.call(request)
        if response.status == RegistrationStatus.accepted:
            LOGGER.info("Connected to central system.")
            self.charger.start()
        else:
            LOGGER.warning("BootNotification rejected!")

    async def send_heartbeat(self):
        request = call.HeartbeatPayload()
        await self.call(request)

    async def send_status_notification(self, connector_id=1, status=ChargePointStatus.available):
        request = call.StatusNotificationPayload(
            connector_id=connector_id,
            error_code="NoError",
            status=status
        )
        await self.call(request)
        
    async def send_start_transaction(self, connector_id=1, id_tag="default-tag"):
         # For 1.6 we might need to send this if authorized locally
         pass

    async def send_stop_transaction(self, transaction_id):
         # Logic for stop transaction
         pass

    # --- Actions Initiated by CSMS ---

    @on(Action.remote_start_transaction)
    async def on_remote_start_transaction(self, id_tag, connector_id=None, **kwargs):
        LOGGER.info(f"Received RemoteStartTransaction for id_tag: {id_tag}")

        if not self.charger.stopped:
             return call_result.RemoteStartTransactionPayload(status=AuthorizationStatus.rejected)

        self.charger.start()
        # In 1.6, we accept, but the transaction ID comes later or we generate one?
        # Typically RemoteStartTransaction returns status. Then CP sends StartTransaction.
        # For this minimal implementation we just say accepted.
        
        return call_result.RemoteStartTransactionPayload(status=AuthorizationStatus.accepted)

    @on(Action.remote_stop_transaction)
    async def on_remote_stop_transaction(self, transaction_id, **kwargs):
        LOGGER.info(f"Received RemoteStopTransaction for transaction_id: {transaction_id}")
        
        if self.charger.stopped:
             return call_result.RemoteStopTransactionPayload(status=AuthorizationStatus.rejected)

        self.charger.stop()
        
        return call_result.RemoteStopTransactionPayload(status=AuthorizationStatus.accepted)

    @on(Action.change_configuration)
    async def on_change_configuration(self, key, value, **kwargs):
        LOGGER.info(f"Received ChangeConfiguration for {key} to {value}")
        
        # Simple mapping examples
        status = ConfigurationStatus.accepted
        
        # TODO: Implement actual configuration change logic if needed
        # For now, just logging it.
        
        return call_result.ChangeConfigurationPayload(status=status)
        
    @on(Action.trigger_message)
    async def on_trigger_message(self, requested_message, **kwargs):
        LOGGER.info(f"Received TriggerMessage for {requested_message}")
        return call_result.TriggerMessagePayload(status=ConfigurationStatus.accepted)

    @on(Action.unlock_connector)
    async def on_unlock_connector(self, connector_id, **kwargs):
        LOGGER.info(f"Received UnlockConnector for {connector_id}")
        return call_result.UnlockConnectorPayload(status=UnlockStatus.unlocked)

    @on(Action.get_diagnostics)
    async def on_get_diagnostics(self, location, **kwargs):
        LOGGER.info(f"Received GetDiagnostics for {location}")
        return call_result.GetDiagnosticsPayload(file_name="diagnostics.log")

    @on(Action.update_firmware)
    async def on_update_firmware(self, location, **kwargs):
        LOGGER.info(f"Received UpdateFirmware from {location}")
        return call_result.UpdateFirmwarePayload()

    @on(Action.reset)
    async def on_reset(self, type, **kwargs):
        LOGGER.info(f"Received Reset type: {type}")
        return call_result.ResetPayload(status=ResetStatus.accepted)

    @on(Action.reserve_now)
    async def on_reserve_now(self, reservation_id, expiry_date, id_tag, connector_id, **kwargs):
        LOGGER.info(f"Received ReserveNow for {id_tag}")
        return call_result.ReserveNowPayload(status=ReservationStatus.accepted)

    @on(Action.cancel_reservation)
    async def on_cancel_reservation(self, reservation_id, **kwargs):
        LOGGER.info(f"Received CancelReservation for {reservation_id}")
        return call_result.CancelReservationPayload(status=CancelReservationStatus.accepted)

    @on(Action.set_charging_profile)
    async def on_set_charging_profile(self, connector_id, cs_charging_profiles, **kwargs):
        LOGGER.info(f"Received SetChargingProfile for connector {connector_id}")
        return call_result.SetChargingProfilePayload(status=ChargingProfileStatus.accepted)

    @on(Action.clear_cache)
    async def on_clear_cache(self, **kwargs):
        LOGGER.info("Received ClearCache")
        return call_result.ClearCachePayload(status=ClearCacheStatus.accepted)

    @on(Action.send_local_list)
    async def on_send_local_list(self, list_version, local_authorization_list, update_type, **kwargs):
        LOGGER.info("Received SendLocalList")
        return call_result.SendLocalListPayload(status=UpdateStatus.accepted)

    @on(Action.get_local_list_version)
    async def on_get_local_list_version(self, **kwargs):
        LOGGER.info("Received GetLocalListVersion")
        return call_result.GetLocalListVersionPayload(list_version=1)

    @on(Action.change_availability)
    async def on_change_availability(self, connector_id, type, **kwargs):
        LOGGER.info(f"Received ChangeAvailability {type} for {connector_id}")
        return call_result.ChangeAvailabilityPayload(status=AvailabilityStatus.accepted)

    @on(Action.get_configuration)
    async def on_get_configuration(self, keys, **kwargs):
        LOGGER.info(f"Received GetConfiguration for {keys}")
        return call_result.GetConfigurationPayload(configuration_key=[])

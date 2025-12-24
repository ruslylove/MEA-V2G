import logging
import asyncio
from ocpp.v16 import ChargePoint as Ocpp16ChargePoint
from ocpp.v16 import call, call_result
from ocpp.v16.enums import Action, RegistrationStatus, AuthorizationStatus, ChargePointStatus
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

    @on(Action.RemoteStartTransaction)
    async def on_remote_start_transaction(self, id_tag, **kwargs):
        LOGGER.info(f"Received RemoteStartTransaction for id_tag: {id_tag}")

        if not self.charger.stopped:
             return call_result.RemoteStartTransactionPayload(status=AuthorizationStatus.rejected)

        self.charger.start()
        # In 1.6, we accept, but the transaction ID comes later or we generate one?
        # Typically RemoteStartTransaction returns status. Then CP sends StartTransaction.
        # For this minimal implementation we just say accepted.
        
        return call_result.RemoteStartTransactionPayload(status=AuthorizationStatus.accepted)

    @on(Action.RemoteStopTransaction)
    async def on_remote_stop_transaction(self, transaction_id, **kwargs):
        LOGGER.info(f"Received RemoteStopTransaction for transaction_id: {transaction_id}")
        
        if self.charger.stopped:
             return call_result.RemoteStopTransactionPayload(status=AuthorizationStatus.rejected)

        self.charger.stop()
        
        return call_result.RemoteStopTransactionPayload(status=AuthorizationStatus.accepted)

    @on(Action.ChangeConfiguration)
    async def on_change_configuration(self, key, value, **kwargs):
        LOGGER.info(f"Received ChangeConfiguration for {key} to {value}")
        
        # Simple mapping examples
        status = 'Accepted'
        
        # TODO: Implement actual configuration change logic if needed
        # For now, just logging it.
        
        return call_result.ChangeConfigurationPayload(status=status)

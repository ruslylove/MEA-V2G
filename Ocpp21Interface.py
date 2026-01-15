import logging
import asyncio
from ocpp.v201 import ChargePoint as Ocpp201ChargePoint
from ocpp.v201 import call, call_result
from ocpp.v201.enums import Action, RegistrationStatus, AuthorizationStatus
from ocpp.routing import on
from ChargerSim import ChargerSim

# NOTE: Currently mapping to 2.0.1 classes as 2.1 is an extension/draft and library support might be limited or identical for basic features.
# If ocpp.v21 becomes available, this should be updated.

logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger('ocpp_21_interface')

class Ocpp21Interface(Ocpp201ChargePoint):
    def __init__(self, id, connection, charger: ChargerSim):
         super().__init__(id, connection)
         self.charger = charger
         self.transaction_id = None

    async def send_boot_notification(self, model="MEA-V2G-01", vendor="RuslyLove"):
        request = call.BootNotificationPayload(
            charging_station={
                "model": model,
                "vendor_name": vendor
            },
            reason="PowerUp"
        )
        response = await self.call(request)
        if response.status == RegistrationStatus.accepted:
            LOGGER.info("Connected to central system (OCPP 2.1).")
            self.charger.start()
        else:
            LOGGER.warning("BootNotification rejected!")

    async def send_heartbeat(self):
        request = call.HeartbeatPayload()
        await self.call(request)

    # Reusing 2.0.1 logic for simplicity
    @on(Action.RequestStartTransaction)
    async def on_request_start_transaction(self, id_token, remote_start_id, **kwargs):
        LOGGER.info(f"Received RequestStartTransaction for id_token: {id_token}")
        
        if not self.charger.stopped:
             return call_result.RequestStartTransactionPayload(status=AuthorizationStatus.rejected)

        self.charger.start()
        self.transaction_id = str(remote_start_id)
        
        return call_result.RequestStartTransactionPayload(status=AuthorizationStatus.accepted, transaction_id=self.transaction_id)

    @on(Action.RequestStopTransaction)
    async def on_request_stop_transaction(self, transaction_id, **kwargs):
        LOGGER.info(f"Received RequestStopTransaction for transaction_id: {transaction_id}")
        
        if self.charger.stopped:
             return call_result.RequestStopTransactionPayload(status=AuthorizationStatus.rejected)

        self.charger.stop()
        self.transaction_id = None
        
        return call_result.RequestStopTransactionPayload(status=AuthorizationStatus.accepted)

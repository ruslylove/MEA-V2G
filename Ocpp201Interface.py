import logging
import asyncio
from ocpp.v201 import ChargePoint as Ocpp201ChargePoint
from ocpp.v201 import call, call_result
from ocpp.v201.enums import Action, RegistrationStatus, AuthorizationStatus, TransactionEventType, ChargingState
from ocpp.routing import on
from ChargerSim import ChargerSim

logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger('ocpp_interface')

class Ocpp201Interface(Ocpp201ChargePoint):
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
            LOGGER.info("Connected to central system.")
            self.charger.start() # Ensure charger is ready (though not charging yet)
        else:
            LOGGER.warning("BootNotification rejected!")

    async def send_heartbeat(self):
        request = call.HeartbeatPayload()
        await self.call(request)

    async def send_status_notification(self, connector_id=1, status=ChargingState.idle):
        # Mapping generic status to OCPP 2.0.1 StatusNotification
        # Note: In 2.0.1 StatusNotification is simpler, often used for error reporting or availability.
        # TransactionEvent is used for charging states.
        request = call.StatusNotificationPayload(
            timestamp=self._get_current_time(),
            connector_status=status,
            evse_id=connector_id,
            connector_id=connector_id # 2.0.1 uses evse_id and connector_id
        )
        await self.call(request)

    async def send_transaction_event(self, event_type: TransactionEventType, seq_no, transaction_id, trigger_reason, charging_state=None):
        payload = {
            "event_type": event_type,
            "timestamp": self._get_current_time(),
            "trigger_reason": trigger_reason,
            "seq_no": seq_no,
            "transaction_info": {
                "transaction_id": transaction_id
            }
        }
        if charging_state:
             # In 2.0.1 we might send evse info
             pass
        
        request = call.TransactionEventPayload(**payload)
        await self.call(request)

    # --- Actions Initiated by CSMS ---

    @on(Action.RequestStartTransaction)
    async def on_request_start_transaction(self, id_token, remote_start_id, **kwargs):
        LOGGER.info(f"Received RequestStartTransaction for id_token: {id_token}")
        
        # Check if charger is already busy?
        if not self.charger.stopped:
             return call_result.RequestStartTransactionPayload(status=AuthorizationStatus.rejected)

        # Start the charger
        self.charger.start()
        self.transaction_id = str(remote_start_id) # Using remote ID as trans ID for simplicity in this mock
        
        # Notify that we accepted
        return call_result.RequestStartTransactionPayload(status=AuthorizationStatus.accepted, transaction_id=self.transaction_id)

    @on(Action.RequestStopTransaction)
    async def on_request_stop_transaction(self, transaction_id, **kwargs):
        LOGGER.info(f"Received RequestStopTransaction for transaction_id: {transaction_id}")
        
        if self.charger.stopped:
             return call_result.RequestStopTransactionPayload(status=AuthorizationStatus.rejected)

        self.charger.stop()
        self.transaction_id = None
        
        return call_result.RequestStopTransactionPayload(status=AuthorizationStatus.accepted)

    @on(Action.SetVariables)
    async def on_set_variables(self, set_variable_data, **kwargs):
        LOGGER.info("Received SetVariables request")
        
        set_variable_result = []

        for variable in set_variable_data:
            component = variable.get('component', {}).get('name')
            var_name = variable.get('variable', {}).get('name')
            value = variable.get('attribute_value')
            
            status = 'Accepted'
            
            # Simple mapping examples
            if component == 'EVSE' and var_name == 'AllowMaxChargingPower':
                 try:
                     self.charger.setEvseMaxPower(float(value))
                 except ValueError:
                     status = 'Rejected'
            elif component == 'EVSE' and var_name == 'CurrentLimit':
                 try:
                     self.charger.setEvseMaxCurrent(float(value))
                 except ValueError:
                     status = 'Rejected'
            else:
                 status = 'UnknownVariable' # Or NotSupported

            set_variable_result.append({
                "component": variable.get('component'),
                "variable": variable.get('variable'),
                "attribute_status": status
            })

        return call_result.SetVariablesPayload(set_variable_result=set_variable_result)

    def _get_current_time(self):
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).isoformat()

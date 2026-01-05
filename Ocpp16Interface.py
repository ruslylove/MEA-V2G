import logging
import asyncio
import time
from datetime import datetime
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
    AvailabilityStatus,
    RemoteStartStopStatus,
    Measurand,
    ReadingContext,
    ValueFormat,
    UnitOfMeasure
)
from ocpp.routing import on
from Charger import Charger

logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger('ocpp_16_interface')

class Ocpp16Interface(Ocpp16ChargePoint):
    def __init__(self, id, connection, charger: Charger):
        super().__init__(id, connection)
        self.charger = charger
        # Transaction Management (Connector ID -> Transaction ID)
        self.transactions = {} 
        # Meter Values Tasks (Connector ID -> Task)
        self._meter_value_tasks = {}
        
        # Configuration Store
        self.configuration = {
            'HeartbeatInterval': '60',
            'ConnectionTimeOut': '60',
            'MeterValueSampleInterval': '60',
            'LocalAuthorizeOffline': 'False',
            'UnlockConnectorOnEVSideDisconnect': 'False',
            'AutoCharge': 'False', # Custom Key
            'MEA_V2G_PowerDemand': '0' # Custom Key
        }

    async def send_boot_notification(self, model="MEA-V2G-01", vendor="KMUTNB"):
        request = call.BootNotification(
            charge_point_model=model,
            charge_point_vendor=vendor
        )
        response = await self.call(request)
        if response.status == RegistrationStatus.accepted:
            LOGGER.info("Connected to central system.")
            self.charger.start()
        else:
            LOGGER.warning("BootNotification rejected!")
        return response 

    async def send_heartbeat(self):
        request = call.Heartbeat()
        await self.call(request)

    async def send_status_notification(self, connector_id=1, status=ChargePointStatus.available, error_code="NoError", info=None):
        payload = {
            'connector_id': connector_id,
            'error_code': error_code,
            'status': status
        }
        if info:
             payload['info'] = info
             
        request = call.StatusNotification(**payload)
        await self.call(request)
        
    async def start_transaction(self, connector_id=1, id_tag="default-tag"):
        """
        Starts a transaction locally and sends StartTransaction to CSMS.
        """
        if connector_id in self.transactions:
             LOGGER.warning(f"Transaction already in progress for connector {connector_id}.")
             return

        # 1. Send StartTransaction
        request = call.StartTransaction(
            connector_id=connector_id,
            id_tag=id_tag,
            meter_start=0, # Simplified
            timestamp=datetime.utcnow().isoformat()
        )
        
        response = await self.call(request)
        
        if response.id_tag_info['status'] == AuthorizationStatus.accepted:
            transaction_id = response.transaction_id
            self.transactions[connector_id] = transaction_id
            LOGGER.info(f"Transaction started on {connector_id}: {transaction_id}")
            self.charger.start()
            
            # Start MeterValues Loop
            self._meter_value_tasks[connector_id] = asyncio.create_task(self._meter_values_loop(connector_id, transaction_id))
        else:
            LOGGER.warning(f"StartTransaction rejected: {response.id_tag_info['status']}")

    async def stop_transaction(self, connector_id=1, reason=None):
        """
        Stops the current transaction and sends StopTransaction to CSMS.
        """
        if connector_id not in self.transactions:
             LOGGER.warning(f"No transaction to stop for connector {connector_id}.")
             # Fallback: try to find any? No, stricter is better for testing.
             return

        transaction_id = self.transactions[connector_id]

        # Stop MeterValues
        if connector_id in self._meter_value_tasks:
            task = self._meter_value_tasks.pop(connector_id)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # Send StopTransaction
        request = call.StopTransaction(
            transaction_id=transaction_id,
            meter_stop=100, # Simplified
            timestamp=datetime.utcnow().isoformat(),
            reason=reason
        )
        
        await self.call(request)
        LOGGER.info(f"Transaction stopped on {connector_id}: {transaction_id}")
        
        self.transactions.pop(connector_id, None)
        # Only stop charger if no transactions left? 
        # Simplified: Stop if no transactions.
        if not self.transactions:
            self.charger.stop()

    async def _meter_values_loop(self, connector_id, transaction_id):
        try:
            while True:
                interval = int(self.configuration.get('MeterValueSampleInterval', 60))
                if interval <= 0:
                    interval = 60
                
                await asyncio.sleep(interval)
                
                # Snapshot values
                voltage = self.charger.getEvsePresentVoltage()
                current = self.charger.getEvsePresentCurrent()
                power = voltage * current
                
                # Prepare MeterValues
                # NOTE: MEA requires specific measurands.
                # Voltage, Current.Import, Energy.Active.Import.Register, Power.Active.Import, SoC, Power.Offered
                
                sampled_values = [
                    {"value": str(voltage), "context": "Sample.Periodic", "format": "Raw", "measurand": "Voltage", "unit": "V"},
                    {"value": str(current), "context": "Sample.Periodic", "format": "Raw", "measurand": "Current.Import", "unit": "A"},
                    {"value": str(power),   "context": "Sample.Periodic", "format": "Raw", "measurand": "Power.Active.Import", "unit": "W"}
                ]
                
                request = call.MeterValues(
                    connector_id=connector_id,
                    transaction_id=transaction_id,
                    meter_value=[{
                        "timestamp": datetime.utcnow().isoformat(),
                        "sampled_value": sampled_values
                    }]
                )
                
                await self.call(request)
                
        except asyncio.CancelledError:
            pass
        except Exception as e:
            LOGGER.error(f"Error in MeterValues loop: {e}")

    # --- Actions Initiated by CSMS ---

    @on(Action.remote_start_transaction)
    async def on_remote_start_transaction(self, id_tag, connector_id=None, **kwargs):
        LOGGER.info(f"Received RemoteStartTransaction for id_tag: {id_tag}")

        target_connector = connector_id or 1
        
        if target_connector in self.transactions:
             return call_result.RemoteStartTransaction(status=RemoteStartStopStatus.rejected)

        # Trigger internal start transaction logic in background to not block response
        asyncio.create_task(self.start_transaction(target_connector, id_tag))
        
        return call_result.RemoteStartTransaction(status=RemoteStartStopStatus.accepted)

    @on(Action.remote_stop_transaction)
    async def on_remote_stop_transaction(self, transaction_id, **kwargs):
        LOGGER.info(f"Received RemoteStopTransaction for transaction_id: {transaction_id}")
        
        target_connector = None
        for cid, tid in self.transactions.items():
            if tid == transaction_id:
                target_connector = cid
                break
        
        if target_connector is None:
             return call_result.RemoteStopTransaction(status=RemoteStartStopStatus.rejected)

        asyncio.create_task(self.stop_transaction(connector_id=target_connector, reason="Remote"))
        
        return call_result.RemoteStopTransaction(status=RemoteStartStopStatus.accepted)

    @on(Action.change_configuration)
    async def on_change_configuration(self, key, value, **kwargs):
        LOGGER.info(f"Received ChangeConfiguration for {key} to {value}")
        
        # Validation Logic can be expanded here
        if key in self.configuration:
             # Type conversion checks could go here
             self.configuration[key] = value
             
             # Specific logic for side-effects
             if key == "MEA_V2G_PowerDemand":
                try:
                    power_watts = int(value)
                    if power_watts < 0:
                        LOGGER.info(f"[V2G] Grid Demand: Discharging {abs(power_watts)} W")
                    elif power_watts > 0:
                        LOGGER.info(f"[V2G] Grid Supply: Charging {power_watts} W")
                    else:
                        LOGGER.info("[V2G] Idle")
                except ValueError:
                    return call_result.ChangeConfiguration(status=ConfigurationStatus.rejected)

             return call_result.ChangeConfiguration(status=ConfigurationStatus.accepted)
        else:
             # Allow unknown keys? Usually Rejected or NotSupported, but for mock flexible
             self.configuration[key] = value # Accept everything for now
             return call_result.ChangeConfiguration(status=ConfigurationStatus.accepted)
        
    @on(Action.get_configuration)
    async def on_get_configuration(self, keys=None, **kwargs):
        LOGGER.info(f"Received GetConfiguration for {keys}")
        
        result_keys = []
        if keys:
            for key in keys:
                if key in self.configuration:
                    result_keys.append({
                        'key': key, 
                        'readonly': False, 
                        'value': self.configuration[key]
                    })
                else:
                    # Unknown key
                    pass
        else:
            # Return all
            for k, v in self.configuration.items():
                result_keys.append({
                    'key': k, 
                    'readonly': False, 
                    'value': v
                })
        
        return call_result.GetConfiguration(configuration_key=result_keys)

    @on(Action.trigger_message)
    async def on_trigger_message(self, requested_message, **kwargs):
        LOGGER.info(f"Received TriggerMessage for {requested_message}")
        
        if requested_message == "BootNotification":
             asyncio.create_task(self.send_boot_notification())
        elif requested_message == "StatusNotification":
             asyncio.create_task(self.send_status_notification())
        elif requested_message == "Heartbeat":
             asyncio.create_task(self.send_heartbeat())
        elif requested_message == "MeterValues":
             # For MeterValues, we might need to send one immediately. 
             # Reusing the loop logic or creating a one-off.
             # Let's create a one-off for now to satisfy the test.
             asyncio.create_task(self.send_meter_values_one_off())

        return call_result.TriggerMessage(status=ConfigurationStatus.accepted)

    async def send_meter_values_one_off(self):
         # Snapshot values
        voltage = self.charger.getEvsePresentVoltage()
        current = self.charger.getEvsePresentCurrent()
        power = voltage * current
        
        sampled_values = [
            {"value": str(voltage), "context": "Sample.Periodic", "format": "Raw", "measurand": "Voltage", "unit": "V"},
            {"value": str(current), "context": "Sample.Periodic", "format": "Raw", "measurand": "Current.Import", "unit": "A"},
            {"value": str(power),   "context": "Sample.Periodic", "format": "Raw", "measurand": "Power.Active.Import", "unit": "W"}
        ]
        
        request = call.MeterValues(
            connector_id=1,
            transaction_id=self.transaction_id,
            meter_value=[{
                "timestamp": datetime.utcnow().isoformat(),
                "sampled_value": sampled_values
            }]
        )
        await self.call(request)

    @on(Action.unlock_connector)
    async def on_unlock_connector(self, connector_id, **kwargs):
        LOGGER.info(f"Received UnlockConnector for {connector_id}")
        return call_result.UnlockConnector(status=UnlockStatus.unlocked)

    @on(Action.get_diagnostics)
    async def on_get_diagnostics(self, location, **kwargs):
        LOGGER.info(f"Received GetDiagnostics for {location}")
        return call_result.GetDiagnostics(file_name="diagnostics.log")

    @on(Action.update_firmware)
    async def on_update_firmware(self, location, **kwargs):
        LOGGER.info(f"Received UpdateFirmware from {location}")
        return call_result.UpdateFirmware()

    @on(Action.reset)
    async def on_reset(self, type, **kwargs):
        LOGGER.info(f"Received Reset type: {type}")
        return call_result.Reset(status=ResetStatus.accepted)

    @on(Action.reserve_now)
    async def on_reserve_now(self, reservation_id, expiry_date, id_tag, connector_id, **kwargs):
        LOGGER.info(f"Received ReserveNow for {id_tag}")
        return call_result.ReserveNow(status=ReservationStatus.accepted)

    @on(Action.cancel_reservation)
    async def on_cancel_reservation(self, reservation_id, **kwargs):
        LOGGER.info(f"Received CancelReservation for {reservation_id}")
        return call_result.CancelReservation(status=CancelReservationStatus.accepted)

    @on(Action.set_charging_profile)
    async def on_set_charging_profile(self, connector_id, cs_charging_profiles, **kwargs):
        LOGGER.info(f"Received SetChargingProfile for connector {connector_id}")
        return call_result.SetChargingProfile(status=ChargingProfileStatus.accepted)

    @on(Action.clear_cache)
    async def on_clear_cache(self, **kwargs):
        LOGGER.info("Received ClearCache")
        return call_result.ClearCache(status=ClearCacheStatus.accepted)

    @on(Action.send_local_list)
    async def on_send_local_list(self, list_version, local_authorization_list, update_type, **kwargs):
        LOGGER.info("Received SendLocalList")
        return call_result.SendLocalList(status=UpdateStatus.accepted)

    @on(Action.get_local_list_version)
    async def on_get_local_list_version(self, **kwargs):
        LOGGER.info("Received GetLocalListVersion")
        return call_result.GetLocalListVersion(list_version=1)

    @on(Action.change_availability)
    async def on_change_availability(self, connector_id, type, **kwargs):
        LOGGER.info(f"Received ChangeAvailability {type} for {connector_id}")
        return call_result.ChangeAvailability(status=AvailabilityStatus.accepted)

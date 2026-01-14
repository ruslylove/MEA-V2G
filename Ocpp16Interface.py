import asyncio
import logging
from datetime import datetime, timezone
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
    UnitOfMeasure,
    DataTransferStatus
)
from ocpp.exceptions import TypeConstraintViolationError, FormatViolationError
from ocpp.routing import on
from Charger import Charger
import queue
import json

# Global queue for tracing packets in tests
PACKET_QUEUE = queue.Queue()

logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger('ocpp_16_interface')
# DEBUG: Write to file
fh = logging.FileHandler('/tmp/evse_debug.log')
fh.setLevel(logging.DEBUG)
LOGGER.addHandler(fh)

class Ocpp16Interface(Ocpp16ChargePoint):
    # Configurable flags
    LOG_SEND_PACKETS = False

    def __init__(self, id, connection, charger=None, evse=None):
        super().__init__(id, connection, response_timeout=20)
        self.charger = charger
        self.evse = evse
        self.heartbeat_interval = 60 # Default
        # Transaction Management (Connector ID -> Transaction ID)
        self.transactions = {} 
        # Reservation Management (Connector ID -> {reservationId, idTag, expiryDate})
        self.reservations = {} 
        # Cache for ConcurrentTx recovery
        self._concurrent_tx_cache = {} 
        self._waiting_for_start_tx = None # connector_id
        # Meter Values Tasks (Connector ID -> Task)
        self._meter_value_tasks = {}
        # Connector Status Tracking
        self.connector_status = {}
        # Offline Mode
        self.is_offline = False
        self.is_offline = False
        self.offline_buffer = []
        # Configuration Store
        self.configuration = {
             'MEAV2G': 'true', 
             'MeterValueSampleInterval': '5', 
             'HeartbeatInterval': '600',
             'LocalAuthorizeOffline': 'false', # Default false for compliance? Or true? 
             # Section 9.3 changes it to false. Default probably true for robustness or false for security?
             # MEA specs usually default false?
             'Power.Active.Import': '0' # For 9.4 test
        }
        
    async def route_message(self, raw_msg):
        # Log raw message for debugging purposes
        # Note: raw_msg is a string usually
        print(f"\n[EVSE] RAW RECV: {raw_msg}", flush=True)
        # Avoid flooding queue with Heartbeats/Boot if unwanted, but valuable for now
        # Parse minimal to see type? 
        # For now, just log everything to see if RemoteStart arrives.
        # PACKET_QUEUE.put(f"[EVSE] RAW RECV: {raw_msg}") 
        # Keeping it out of queue to avoid messing up test logic assertions? 
        # User wants "log the ocpp message". So I SHOULD put it in queue!
        if "[" in raw_msg and "ConcurrentTx" in raw_msg:
             try:
                 import json
                 msg = json.loads(raw_msg)
                 # Check if it's a CallResult (type 3) and contains idTagInfo with ConcurrentTx
                 # [3, "UniqueId", Payload]
                 if isinstance(msg, list) and len(msg) == 3 and msg[0] == 3:
                     unique_id = msg[1]
                     payload = msg[2]
                     if isinstance(payload, dict):
                         status = payload.get('idTagInfo', {}).get('status')
                         tid = payload.get('transactionId')
                         if status == 'ConcurrentTx' and tid:
                              # Direct Recovery via Pending Flag
                              LOGGER.info(f"SNIFFER: Checking Flag on obj {id(self)}: {self._waiting_for_start_tx}")
                              if self._waiting_for_start_tx:
                                   cid = self._waiting_for_start_tx
                                   LOGGER.info(f"SNIFFER: Intercepted ConcurrentTx ID {tid} for pending Connector {cid}")
                                   self.transactions[cid] = tid
                                   
                                   msg_tx = f"Transaction started on {cid}: {tid}"
                                   LOGGER.info(msg_tx)
                                   PACKET_QUEUE.put(msg_tx)
                                   
                                   # We can't easily start the charger logic here because it's async?
                                   # Actually we can let the main loop discover it or do it here?
                                   # Doing it in start_transaction is safer if we just suppress exception.
                              else:
                                   LOGGER.warning(f"SNIFFER: Cached ConcurrentTx ID {tid} but no pending start_tx")
             except Exception as e:
                 LOGGER.error(f"SNIFFER ERROR: {e}")

        if self.LOG_SEND_PACKETS:
            PACKET_QUEUE.put(f"[EVSE] RAW RECV: {raw_msg}")
        await super().route_message(raw_msg)
        
    async def call(self, payload, suppress=False):
        """
        Override call to support Offline buffering.
        """
        if self.is_offline:
            print(f"[EVSE] OFFLINE: Buffering {payload}", flush=True)
            self.offline_buffer.append(payload)
            
            # Construct Mock Response
            # We need to return a CallResult compatible object or Payload.
            # ocpp library returns the Payload object (e.g. StartTransactionConf) directly if unpacked?
            # No, self.call returns the Payload object (e.g. StartTransactionConf) directly.
            
            action_name = payload.__class__.__name__.replace('Payload', '')
             # Map request to response class?
            # Actually, we can return a Dummy object with necessary attributes.
            # Most checks just look for 'status' or 'idTagInfo'.
            
            simulated_response = call_result.StartTransaction(
                    transaction_id=1,
                    id_tag_info={'status': 'Accepted', 'expiryDate': datetime.now(timezone.utc).isoformat(), 'parentIdTag': 'OFFLINE'}
                ) if action_name == 'StartTransaction' else None
            
            if not simulated_response:
                 # Generic response with status Accepted if possible
                 try:
                     # Try to see if we can instantiate Conf class
                     # This is hard generic. 
                     # For 7.7 tests we need: StartTx -> Conf, StopTx -> Conf.
                     if action_name == 'StopTransaction':
                         simulated_response = call_result.StopTransaction(
                             id_tag_info={'status': 'Accepted'}
                         )
                 except:
                     pass
            
            if not simulated_response:
                 # Fallback for StatusNotification etc. (returns empty payload)
                simulated_response = call_result.StatusNotification() if action_name == 'StatusNotification' else None

            # If still None, just return object
            if not simulated_response:
                class DummyResp:
                     pass
                simulated_response = DummyResp()
            
            return simulated_response

        return await super().call(payload, suppress)

    def go_offline(self):
        self.is_offline = True
        print("[EVSE] Went OFFLINE", flush=True)

    async def go_online(self):
        self.is_offline = False
        print(f"[EVSE] Went ONLINE. Flushing {len(self.offline_buffer)} messages...", flush=True)
        while self.offline_buffer:
            msg = self.offline_buffer.pop(0)
            print(f"[EVSE] FLUSHING: {msg}", flush=True)
            try:
                await super().call(msg)
                # Small delay to keep order
                await asyncio.sleep(0.1)
            except Exception as e:
                LOGGER.error(f"Error flushing offline message: {e}")

        
        # Configuration Store
        self.configuration = {
            'HeartbeatInterval': '60',
            'ConnectionTimeOut': '60',
            'MeterValueSampleInterval': '60',
            'LocalAuthorizeOffline': 'False',
            'UnlockConnectorOnEVSideDisconnect': 'False',
            'AutoCharge': 'False',
            'MEA_V2G_PowerDemand': '0',
            'MEAV2G': 'true',
            'Power.Active.Import': '0'
        }

    async def send_boot_notification(self, model="MEA-V2G-01", vendor="KMUTNB"):
        request = call.BootNotification(
            charge_point_model=model,
            charge_point_vendor=vendor
        )
        msg_send = f"[EVSE] SENDING Packet: BootNotification\nPayload: {json.dumps({'chargePointModel': model, 'chargePointVendor': vendor}, indent=2)}"
        if self.LOG_SEND_PACKETS:
            print(f"\n{msg_send}", flush=True)
            PACKET_QUEUE.put(msg_send)
        response = await self.call(request)
        if response.status == RegistrationStatus.accepted:
            msg = "[EVSE] BootNotification ACCEPTED"
            print(f"\n{msg}", flush=True)
            PACKET_QUEUE.put(msg)
            LOGGER.info("Connected to central system.")
            self.charger.start()
            # Start Heartbeat Loop
            asyncio.create_task(self._heartbeat_loop())
            # Start Reservation GC Loop
            asyncio.create_task(self._reservation_loop())
        else:
            LOGGER.warning("BootNotification rejected!")
        return response 

    async def _heartbeat_loop(self):
        try:
            while True:
                interval = int(self.configuration.get('HeartbeatInterval', 60))
                if interval <= 0:
                    interval = 60
                await asyncio.sleep(interval)
                await self.send_heartbeat()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            LOGGER.error(f"Error in Heartbeat loop: {e}") 

    async def _reservation_loop(self):
        """Background task to check for expired reservations."""
        try:
            while True:
                await asyncio.sleep(1) # Check every second
                
                now = datetime.now(timezone.utc)
                to_remove = []
                
                for connector_id, res in self.reservations.items():
                    expiry_str = res.get('expiryDate')
                    if expiry_str:
                        # Parse expiry date (Handle Z for UTC)
                        try:
                            if expiry_str.endswith('Z'):
                                expiry_str = expiry_str[:-1] + '+00:00'
                            expiry_dt = datetime.fromisoformat(expiry_str)
                            if expiry_dt.tzinfo is None:
                                expiry_dt = expiry_dt.replace(tzinfo=timezone.utc)
                            
                            if now > expiry_dt:
                                print(f"[EVSE] Reservation {res['reservationId']} expired at {expiry_str}. Current: {now}")
                                to_remove.append(connector_id)
                        except ValueError as e:
                            LOGGER.error(f"Error parsing expiry date {expiry_str}: {e}")
                
                for connector_id in to_remove:
                    LOGGER.info(f"Removing expired reservation for connector {connector_id}")
                    if connector_id in self.reservations:
                        del self.reservations[connector_id]
                        # Send Status Available
                        if self.evse:
                             self.evse.set_status("Available")
                        else:
                             await self.send_status_notification(connector_id, ChargePointStatus.available)

        except asyncio.CancelledError:
            pass
        except Exception as e:
            LOGGER.error(f"Error in Reservation loop: {e}") 

    async def send_heartbeat(self):
        request = call.Heartbeat()
        msg_send = "[EVSE] SENDING Packet: Heartbeat"
        if self.LOG_SEND_PACKETS:
            print(f"\n{msg_send}", flush=True)
            PACKET_QUEUE.put(msg_send)
        response = await self.call(request)
        msg = f"[EVSE] Heartbeat ACKNOWLEDGED (Time: {response.current_time})"
        print(f"\n{msg}", flush=True)
        PACKET_QUEUE.put(msg)

    async def send_status_notification(self, connector_id=1, status=ChargePointStatus.available, error_code="NoError", info=None):
        payload = {
            'connector_id': connector_id,
            'error_code': error_code,
            'status': status,
            'timestamp': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
            'vendor_id': "KMUTNB",
            'vendor_error_code': ""
        }
        if info:
             payload['info'] = info
             
        request = call.StatusNotification(**payload)
        
        # Update local state
        self.connector_status[connector_id] = status

        msg_send = f"[EVSE] SENDING Packet: StatusNotification ({status})"
        if self.LOG_SEND_PACKETS:
            print(f"\n{msg_send}", flush=True)
            PACKET_QUEUE.put(msg_send)
    
        response = await self.call(request)
        msg = f"[EVSE] StatusNotification ACKNOWLEDGED ({status})"
        print(f"\n{msg}", flush=True)
        PACKET_QUEUE.put(msg)
        
    async def send_authorize(self, id_tag="RFID_SIM"):
        request = call.Authorize(id_tag=id_tag)
        msg_send = f"[EVSE] SENDING Packet: Authorize (idTag={id_tag})"
        if self.LOG_SEND_PACKETS:
            print(f"\n{msg_send}", flush=True)
            PACKET_QUEUE.put(msg_send)
        response = await self.call(request)
        msg = f"[EVSE] Authorize ACKNOWLEDGED ({response.id_tag_info['status']})"
        print(f"\n{msg}", flush=True)
        PACKET_QUEUE.put(msg)
        return response.id_tag_info['status']

    async def start_transaction(self, connector_id=1, id_tag="default-tag"):
        """
        Starts a transaction locally and sends StartTransaction to CSMS.
        """
        if connector_id in self.transactions:
             LOGGER.warning(f"Transaction already in progress for connector {connector_id}.")
             if self.charger.is_charging():
                print("[Sim] Charging already in progress for StartTransaction request")
                return

        # Prepare payload
        request_payload = {
            'connector_id': connector_id,
            'id_tag': id_tag if id_tag else "UnknownTag",
            'meter_start': 0, # Simplified
            'timestamp': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        }
        
        # Check active reservation for this connector/tag
        # Ideally we'd match parentIdTag etc, but simplified:
        if connector_id in self.reservations:
            res = self.reservations[connector_id]
            # Expired? match idTag? (Simplified: Just use it if it exists)
            # In 5.13 we need to send reservationId.
            request_payload['reservation_id'] = res['reservationId']
            # Consume reservation
            del self.reservations[connector_id]
            LOGGER.info(f"Consuming Reservation {request_payload['reservation_id']} for transaction.")

        # Send StatusNotification (Preparing) - MEA Requirement
        await self.send_status_notification(connector_id, ChargePointStatus.preparing)

        request = call.StartTransaction(**request_payload)

        msg_send = f"[EVSE] SENDING Packet: StartTransaction\nPayload: {json.dumps(request_payload, indent=2)}"
        if self.LOG_SEND_PACKETS:
            print(f"\n{msg_send}", flush=True)
            PACKET_QUEUE.put(msg_send)
        
        self._waiting_for_start_tx = connector_id
        LOGGER.info(f"StartTransaction: Set Flag on obj {id(self)} to {connector_id}")
        try:
            response = await self.call(request)
            self._waiting_for_start_tx = None # Clear on success
            # Response is valid
            if response.id_tag_info['status'] == AuthorizationStatus.accepted or response.id_tag_info['status'] == "ConcurrentTx":
                 LOGGER.info("Transaction Accepted")
                 self.transactions[connector_id] = response.transaction_id
                 msg_tx = f"Transaction started on {connector_id}: {response.transaction_id}"
                 LOGGER.info(msg_tx)
                 PACKET_QUEUE.put(msg_tx)
                 
                 self.charger.start()
                 # Send StatusNotification (Charging)
                 await self.send_status_notification(connector_id, ChargePointStatus.charging)
                 self._meter_value_tasks[connector_id] = asyncio.create_task(self._meter_values_loop(connector_id, response.transaction_id))
            else:
                 LOGGER.warning(f"Transaction Rejected: {response.id_tag_info['status']}")

        except (TypeConstraintViolationError, FormatViolationError, Exception) as e:
             self._waiting_for_start_tx = None # Clear on error
             
             # Check if Sniffer handled it
             if connector_id in self.transactions:
                  tid = self.transactions[connector_id]
                  LOGGER.info(f"Transaction ID {tid} recovered via SNIFFER (Pending Flag).")
                  # Complete the setup
                  self.charger.start()
                  await self.send_status_notification(connector_id, ChargePointStatus.charging)
                  self._meter_value_tasks[connector_id] = asyncio.create_task(self._meter_values_loop(connector_id, tid))
                  return

             LOGGER.warning(f"StartTransaction Warning (likely type mismatch or structure): {e}")
             
             # Recovery Logic (Regex fallback)
             import re
             # Extract transactionId and status from string representation of the exception/message
             match = re.search(r"'transactionId':\s*'?(-?\d+|[a-zA-Z0-9_\-]+)'?", str(e))
             
             status = 'Accepted' # Default
             if "'status': 'Invalid'" in str(e): status = "Invalid"
             if "'status': 'ConcurrentTx'" in str(e): status = "ConcurrentTx"
             
             tid = None
             if match:
                  tid_str = match.group(1)
                  # If ID is -1 and status Invalid, it is Rejected
                  if tid_str == '-1' and status == 'Invalid':
                       LOGGER.warning("Transaction Rejected by CSMS (Invalid Tag).")
                       return
                  
                  try:
                      tid = int(tid_str)
                  except:
                      tid = tid_str
                  
                  if status == 'Invalid':
                       LOGGER.warning(f"Transaction Rejected (Invalid) with ID {tid}")
                       return

             # Fallback: Check Sniffer Cache for ConcurrentTx
             # We rely on request.unique_id to link the request to the cached response
             if not tid and hasattr(self, '_concurrent_tx_cache') and request.unique_id in self._concurrent_tx_cache:
                  tid = int(self._concurrent_tx_cache[request.unique_id])
                  status = "ConcurrentTx"
                  LOGGER.info(f"Recovered Transaction ID {tid} from SNIFFER cache.")

             if tid:
                  LOGGER.info(f"Recovered Transaction ID {tid} (Status: {status}) via RECOVERY.")
                  self.transactions[connector_id] = tid
                  
                  msg_tx = f"Transaction started on {connector_id}: {tid}"
                  LOGGER.info(msg_tx)
                  PACKET_QUEUE.put(msg_tx)
                  
                  if status == 'Accepted' or status == 'ConcurrentTx':
                      self.charger.start()
                      await self.send_status_notification(connector_id, ChargePointStatus.charging)
                      self._meter_value_tasks[connector_id] = asyncio.create_task(self._meter_values_loop(connector_id, tid))
             else:
                  LOGGER.error("Could not recover Transaction ID from exception.")

    async def stop_transaction(self, connector_id=1, reason=None):
        """
        Stops the current transaction and sends StopTransaction to CSMS.
        """
        if connector_id not in self.transactions:
             LOGGER.warning(f"No transaction to stop for connector {connector_id}.")
             # Fallback: try to find any? No, stricter is better for testing.
             return

        transaction_id = self.transactions[connector_id]
        # Ensure int
        try:
             transaction_id = int(transaction_id)
        except ValueError:
             LOGGER.warning(f"Could not cast transaction_id {transaction_id} to int. Keeping original.")

        # Stop MeterValues
        if connector_id in self._meter_value_tasks:
            task = self._meter_value_tasks.pop(connector_id)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # Send StatusNotification (Finishing)
        await self.send_status_notification(connector_id, "Finishing")

        # Send StopTransaction
        request = call.StopTransaction(
            transaction_id=transaction_id,
            meter_stop=100, # Simplified
            timestamp=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
            reason=reason
        )
        
        msg_send = f"[EVSE] SENDING Packet: StopTransaction (ID: {transaction_id}, Reason: {reason})"
        if self.LOG_SEND_PACKETS:
            print(f"\n{msg_send}", flush=True)
            PACKET_QUEUE.put(msg_send)

        await self.call(request)
        msg_stop = f"Transaction stopped on {connector_id}: {transaction_id}"
        LOGGER.info(msg_stop)
        PACKET_QUEUE.put(msg_stop)
        
        self.transactions.pop(connector_id, None)
        
        # Send StatusNotification (Available)
        await self.send_status_notification(connector_id, "Available")

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
                        "timestamp": datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
                        "sampled_value": sampled_values
                    }]
                )
                
                msg_send = f"[EVSE] SENDING Packet: MeterValues (TxID: {transaction_id})"
                if self.LOG_SEND_PACKETS:
                    print(f"\n{msg_send}", flush=True)
                    PACKET_QUEUE.put(msg_send)

                await self.call(request)
                msg_mv = f"[EVSE] MeterValues ACKNOWLEDGED"
                print(f"\n{msg_mv}", flush=True)
                PACKET_QUEUE.put(msg_mv)
                
        except asyncio.CancelledError:
            pass
        except Exception as e:
            LOGGER.error(f"Error in MeterValues loop: {e}")

    # --- Actions Initiated by CSMS ---

    @on(Action.remote_start_transaction)
    async def on_remote_start_transaction(self, id_tag, connector_id=None, **kwargs):
        payload = {"idTag": id_tag, "connectorId": connector_id}
        payload.update(kwargs)
        msg = f"[EVSE] RECV Packet: RemoteStartTransaction\nPayload: {json.dumps(payload, indent=2)}"
        print(f"\n{msg}", flush=True)
        PACKET_QUEUE.put(msg)
        LOGGER.info(f"Received RemoteStartTransaction for id_tag: {id_tag}")

        target_connector = connector_id or 1
        
        # Check 1: Connector Unplugged (Available) -> Reject (MEA Requirement)
        current_status = self.connector_status.get(target_connector, ChargePointStatus.available)
        if current_status == ChargePointStatus.available:
             LOGGER.warning(f"RemoteStart rejected: Connector {target_connector} is Unplugged (Available)")
             
             msg_resp = f"[EVSE] Sending RemoteStartTransaction Response (Rejected)"
             print(f"\n{msg_resp}", flush=True)
             PACKET_QUEUE.put(msg_resp)
             
             return call_result.RemoteStartTransaction(status=RemoteStartStopStatus.rejected)

        if target_connector in self.transactions:
             return call_result.RemoteStartTransaction(status=RemoteStartStopStatus.rejected)

        # Trigger internal start transaction logic in background to not block response
        if self.evse:
             print(f"[OCPP] Delegate RemoteStart to EVSE Logic for {id_tag}")
             self.evse.ocpp_authorize(id_tag)
             # We rely on EVSE to trigger the actual StartTransaction packet when session is ready
             # But if EVSE is not in loop (e.g. pre-check), we might miss it?
             # Actually, if we just authorize, the EVSE loop (handleRequestAuthorization) picks it up.
             # Does Evse loop run if no car is plugged?
             # Evse loop waits for EV connection.
             # If RemoteStart comes BEFORE plugin, we set authorized_id_tag.
             # Then user plugs in. Evse checks auth. Uses it. Starts Tx. Correct.
             pass
        else:
             asyncio.create_task(self.start_transaction(target_connector, id_tag))
        
        msg_resp = f"[EVSE] Sending RemoteStartTransaction Response (Accepted)"
        print(f"\n{msg_resp}", flush=True)
        PACKET_QUEUE.put(msg_resp)

        return call_result.RemoteStartTransaction(status=RemoteStartStopStatus.accepted)

    @on(Action.remote_stop_transaction)
    async def on_remote_stop_transaction(self, transaction_id):
        LOGGER.info(f"Received RemoteStopTransaction for transaction_id: {transaction_id}")
        
        # Stop transaction locally?
        # Find connector
        target_connector = None
        for conn_id, tid in self.transactions.items():
            if tid == transaction_id:
                target_connector = conn_id
                break
        
        if target_connector:
             asyncio.create_task(self.stop_transaction(target_connector, reason="Remote"))
             
             msg_resp = f"[EVSE] Sending RemoteStopTransaction Response (Accepted)"
             print(f"\n{msg_resp}", flush=True)
             PACKET_QUEUE.put(msg_resp)
             return call_result.RemoteStopTransaction(status=RemoteStartStopStatus.accepted)
        else:
             LOGGER.warning(f"RemoteStop failed: Transaction {transaction_id} not found.")
             msg_resp = f"[EVSE] Sending RemoteStopTransaction Response (Rejected)"
             print(f"\n{msg_resp}", flush=True)
             PACKET_QUEUE.put(msg_resp)
             return call_result.RemoteStopTransaction(status=RemoteStartStopStatus.rejected)

    @on(Action.send_local_list)
    async def on_send_local_list(self, list_version, local_authorization_list, update_type):
        msg = f"[EVSE] RECV Packet: SendLocalList (ver={list_version}, type={update_type}, len={len(local_authorization_list)})"
        print(f"\n{msg}", flush=True)
        PACKET_QUEUE.put(msg)
        LOGGER.info(msg)
        return call_result.SendLocalList(status=UpdateStatus.accepted)

    @on(Action.change_configuration)
    async def on_change_configuration(self, key, value, **kwargs):
        payload = {"key": key, "value": value}
        payload.update(kwargs)
        msg = f"[EVSE] RECV Packet: ChangeConfiguration\nPayload: {json.dumps(payload, indent=2)}"
        print(f"\n{msg}", flush=True)
        PACKET_QUEUE.put(msg)
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

             msg_resp = f"[EVSE] Sending ChangeConfiguration Response (Accepted)"
             print(f"\n{msg_resp}", flush=True)
             PACKET_QUEUE.put(msg_resp)
             return call_result.ChangeConfiguration(status=ConfigurationStatus.accepted)
        else:
             # Allow unknown keys? Usually Rejected or NotSupported, but for mock flexible
             self.configuration[key] = value # Accept everything for now
             
             msg_resp = f"[EVSE] Sending ChangeConfiguration Response (Accepted)"
             print(f"\n{msg_resp}", flush=True)
             PACKET_QUEUE.put(msg_resp)
             
             return call_result.ChangeConfiguration(status=ConfigurationStatus.accepted)
        
    @on(Action.get_configuration)
    async def on_get_configuration(self, keys=None, **kwargs):
        payload = {"keys": keys}
        payload.update(kwargs)
        msg = f"[EVSE] RECV Packet: GetConfiguration\nPayload: {json.dumps(payload, indent=2)}"
        print(f"\n{msg}", flush=True)
        PACKET_QUEUE.put(msg)
        LOGGER.info(f"Received GetConfiguration for {keys}")
        
        result_keys = []
        unknown_keys = []
        if keys:
            for key in keys:
                if key in self.configuration:
                    result_keys.append({
                        'key': key, 
                        'readonly': False, 
                        'value': self.configuration[key]
                    })
                else:
                    unknown_keys.append(key)
        else:
            # Return all
            for k, v in self.configuration.items():
                result_keys.append({
                    'key': k, 
                    'readonly': False, 
                    'value': v
                })
        
        msg_resp = f"[EVSE] Sending GetConfiguration Response (Count: {len(result_keys)})"
        print(f"\n{msg_resp}", flush=True)
        PACKET_QUEUE.put(msg_resp)
        return call_result.GetConfiguration(configuration_key=result_keys, unknown_key=unknown_keys)

    @on(Action.trigger_message)
    async def on_trigger_message(self, requested_message, **kwargs):
        msg = f"[EVSE] RECV Packet: TriggerMessage ({requested_message})"
        print(f"\n{msg}", flush=True)
        PACKET_QUEUE.put(msg)
        LOGGER.info(f"Received TriggerMessage for {requested_message}")
        
        if requested_message == "BootNotification":
             asyncio.create_task(self.send_boot_notification())
        elif requested_message == "StatusNotification":
             asyncio.create_task(self.send_status_notification())
        elif requested_message == "Heartbeat":
             asyncio.create_task(self.send_heartbeat())
        elif requested_message == "MeterValues":
             asyncio.create_task(self.send_meter_values_one_off())

        msg_resp = f"[EVSE] Sending TriggerMessage Response (Accepted)"
        print(f"\n{msg_resp}", flush=True)
        PACKET_QUEUE.put(msg_resp)
        return call_result.TriggerMessage(status=ConfigurationStatus.accepted)

    async def send_meter_values_one_off(self, connector_id=1):
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
            connector_id=connector_id,
            transaction_id=self.transactions.get(connector_id),
            meter_value=[{
                "timestamp": datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
                "sampled_value": sampled_values
            }]
        )
        msg_send = f"[EVSE] SENDING Packet: MeterValues (One-off)"
        if self.LOG_SEND_PACKETS:
            print(f"\n{msg_send}", flush=True)
            PACKET_QUEUE.put(msg_send)
        await self.call(request)
        msg_mv = f"[EVSE] MeterValues ACKNOWLEDGED"
        print(f"\n{msg_mv}", flush=True)
        PACKET_QUEUE.put(msg_mv)

    @on(Action.unlock_connector)
    async def on_unlock_connector(self, connector_id, **kwargs):
        msg = f"[EVSE] RECV Packet: UnlockConnector ({connector_id})"
        print(f"\n{msg}", flush=True)
        PACKET_QUEUE.put(msg)
        LOGGER.info(f"Received UnlockConnector for {connector_id}")
        
        msg_resp = f"[EVSE] Sending UnlockConnector Response (Unlocked)"
        print(f"\n{msg_resp}", flush=True)
        PACKET_QUEUE.put(msg_resp)
        return call_result.UnlockConnector(status=UnlockStatus.unlocked)

    @on(Action.get_diagnostics)
    async def on_get_diagnostics(self, location, **kwargs):
        msg = f"[EVSE] RECV Packet: GetDiagnostics ({location})"
        print(f"\n{msg}", flush=True)
        PACKET_QUEUE.put(msg)
        LOGGER.info(f"Received GetDiagnostics for {location}")
        
        msg_resp = f"[EVSE] Sending GetDiagnostics Response (fileName: diagnostics.log)"
        print(f"\n{msg_resp}", flush=True)
        PACKET_QUEUE.put(msg_resp)
        return call_result.GetDiagnostics(file_name="diagnostics.log")

    @on(Action.update_firmware)
    async def on_update_firmware(self, location, **kwargs):
        msg = f"[EVSE] RECV Packet: UpdateFirmware ({location})"
        print(f"\n{msg}", flush=True)
        PACKET_QUEUE.put(msg)
        LOGGER.info(f"Received UpdateFirmware from {location}")
        
        msg_resp = f"[EVSE] Sending UpdateFirmware Response (Accepted)"
        print(f"\n{msg_resp}", flush=True)
        PACKET_QUEUE.put(msg_resp)
        return call_result.UpdateFirmware()

    @on(Action.reset)
    async def on_reset(self, type, **kwargs):
        payload = {"type": type}
        payload.update(kwargs)
        msg = f"[EVSE] RECV Packet: Reset\nPayload: {json.dumps(payload, indent=2)}"
        print(f"\n{msg}", flush=True)
        PACKET_QUEUE.put(msg)
        LOGGER.info(f"Received Reset type: {type}")
        msg_resp = f"[EVSE] Sending Reset Response (Accepted)"
        print(f"\n{msg_resp}", flush=True)
        PACKET_QUEUE.put(msg_resp)
        return call_result.Reset(status=ResetStatus.accepted)

    @on(Action.reserve_now)
    async def on_reserve_now(self, reservation_id, expiry_date, id_tag, connector_id, **kwargs):
        payload = {
            "reservationId": reservation_id,
            "expiryDate": expiry_date,
            "idTag": id_tag,
            "connectorId": connector_id
        }
        payload.update(kwargs)
        msg = f"[EVSE] RECV Packet: ReserveNow\nPayload: {json.dumps(payload, indent=2)}"
        print(f"\n{msg}", flush=True)
        PACKET_QUEUE.put(msg)
        LOGGER.info(f"Received ReserveNow for {id_tag}")
        
        # Store reservation
        self.reservations[connector_id] = {
            "reservationId": reservation_id,
            "idTag": id_tag,
            "expiryDate": expiry_date
        }
        
        # Sync state with Evse
        if self.evse:
             self.evse.set_status("Reserved", expiry_date=expiry_date)
        else:
             asyncio.create_task(self.send_status_notification(connector_id, ChargePointStatus.reserved))
        
        msg_resp = f"[EVSE] Sending ReserveNow Response (Accepted)"
        print(f"\n{msg_resp}", flush=True)
        PACKET_QUEUE.put(msg_resp)
        return call_result.ReserveNow(status=ReservationStatus.accepted)

    @on(Action.cancel_reservation)
    async def on_cancel_reservation(self, reservation_id, **kwargs):
        payload = {"reservationId": reservation_id}
        payload.update(kwargs)
        msg = f"[EVSE] RECV Packet: CancelReservation\nPayload: {json.dumps(payload, indent=2)}"
        print(f"\n{msg}", flush=True)
        PACKET_QUEUE.put(msg)
        LOGGER.info(f"Received CancelReservation for {reservation_id}")
        
        # Remove reservation if exists
        target_conn = None
        for conn_id, res in list(self.reservations.items()):
            if res["reservationId"] == reservation_id:
                target_conn = conn_id
                del self.reservations[conn_id]
                break
        
        if target_conn:
             # Sync state with Evse
             if self.evse:
                  self.evse.set_status("Available")
             else:
                  asyncio.create_task(self.send_status_notification(target_conn, ChargePointStatus.available))
        
        msg_resp = f"[EVSE] Sending CancelReservation Response (Accepted)"
        print(f"\n{msg_resp}", flush=True)
        PACKET_QUEUE.put(msg_resp)
        return call_result.CancelReservation(status=CancelReservationStatus.accepted)
        msg_resp = f"[EVSE] Sending CancelReservation Response (Accepted)"
        print(f"\n{msg_resp}", flush=True)
        PACKET_QUEUE.put(msg_resp)
        return call_result.CancelReservation(status=CancelReservationStatus.accepted)

    @on(Action.set_charging_profile)
    async def on_set_charging_profile(self, connector_id, cs_charging_profiles, **kwargs):
        payload = {"connectorId": connector_id, "csChargingProfiles": cs_charging_profiles}
        payload.update(kwargs)
        msg = f"[EVSE] RECV Packet: SetChargingProfile\nPayload: {json.dumps(payload, indent=2)}"
        print(f"\n{msg}", flush=True)
        PACKET_QUEUE.put(msg)
        LOGGER.info(f"Received SetChargingProfile for connector {connector_id}")
        msg_resp = f"[EVSE] Sending SetChargingProfile Response (Accepted)"
        print(f"\n{msg_resp}", flush=True)
        PACKET_QUEUE.put(msg_resp)
        return call_result.SetChargingProfile(status=ChargingProfileStatus.accepted)

    @on(Action.get_local_list_version)
    async def on_get_local_list_version(self, **kwargs):
        msg = f"[EVSE] RECV Packet: GetLocalListVersion"
        print(f"\n{msg}", flush=True)
        PACKET_QUEUE.put(msg)
        LOGGER.info("Received GetLocalListVersion")
        
        msg_resp = f"[EVSE] Sending GetLocalListVersion Response (Version: 1)"
        print(f"\n{msg_resp}", flush=True)
        PACKET_QUEUE.put(msg_resp)
        return call_result.GetLocalListVersion(list_version=1)

    @on(Action.change_availability)
    async def on_change_availability(self, connector_id, type, **kwargs):
        msg = f"[EVSE] RECV Packet: ChangeAvailability ({type} for {connector_id})"
        print(f"\n{msg}", flush=True)
        PACKET_QUEUE.put(msg)
        LOGGER.info(msg)

        status = AvailabilityStatus.accepted
        if type == "Inoperative":
             if self.evse:
                  self.evse.set_status("Unavailable")
             else:
                  asyncio.create_task(self.send_status_notification(connector_id, ChargePointStatus.unavailable))
        elif type == "Operative":
             if self.evse:
                  # If we were Faulted, maybe check if we can go Available?
                  # Simplified: Go Available
                  self.evse.set_status("Available")
             else:
                  asyncio.create_task(self.send_status_notification(connector_id, ChargePointStatus.available))
        
        msg_resp = f"[EVSE] Sending ChangeAvailability Response (Accepted)"
        print(f"\n{msg_resp}", flush=True)
        PACKET_QUEUE.put(msg_resp)
        return call_result.ChangeAvailability(status=AvailabilityStatus.accepted)

    @on(Action.data_transfer)
    async def on_data_transfer(self, vendor_id, message_id=None, data=None, **kwargs):
        msg = f"[EVSE] RECV Packet: DataTransfer ({vendor_id}, {message_id})"
        print(f"\n{msg}", flush=True)
        PACKET_QUEUE.put(msg)
        LOGGER.info(msg)
        
        msg_resp = f"[EVSE] Sending DataTransfer Response (Accepted)"
        print(f"\n{msg_resp}", flush=True)
        PACKET_QUEUE.put(msg_resp)
        return call_result.DataTransfer(status=DataTransferStatus.accepted, data="Pong")

    @on(Action.clear_cache)
    async def on_clear_cache(self, **kwargs):
        msg = f"[EVSE] RECV Packet: ClearCache"
        print(f"\n{msg}", flush=True)
        PACKET_QUEUE.put(msg)
        LOGGER.info(msg)
        
        msg_resp = f"[EVSE] Sending ClearCache Response (Accepted)"
        print(f"\n{msg_resp}", flush=True)
        PACKET_QUEUE.put(msg_resp)
        return call_result.ClearCache(status=ClearCacheStatus.accepted)

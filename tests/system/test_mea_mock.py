import asyncio
import websockets
import sys
import os
import logging
import time
from datetime import datetime

# Add parent directory to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from ocpp.routing import on
from ocpp.v16 import ChargePoint as Cp
from ocpp.v16 import call, call_result
from ocpp.v16.enums import Action, RegistrationStatus, AuthorizationStatus, DataTransferStatus
from Ocpp16Interface import Ocpp16Interface

# Configure logging
logging.basicConfig(level=logging.ERROR) # Reduce noise
logger = logging.getLogger('MockSystemTest')
logger.setLevel(logging.INFO)

# Global reference to the connected server-side ChargePoint instance
connected_csms = None
server_ready = asyncio.Event()

class MockCSMS(Cp):
    async def on_connect(self):
        global connected_csms
        connected_csms = self
        logger.info(f"CSMS: Client connected! {self.id}")
        server_ready.set()

    @on(Action.boot_notification)
    async def on_boot_notification(self, **kwargs):
        logger.info(f"CSMS: Received BootNotification: {kwargs}")
        return call_result.BootNotification(
            current_time=datetime.utcnow().isoformat(),
            interval=10,
            status=RegistrationStatus.accepted
        )

    @on(Action.status_notification)
    async def on_status_notification(self, **kwargs):
        logger.info(f"CSMS: Received StatusNotification: {kwargs}")
        return call_result.StatusNotification()

    @on(Action.heartbeat)
    async def on_heartbeat(self, **kwargs):
        logger.info("CSMS: Received Heartbeat")
        return call_result.Heartbeat(
            current_time=datetime.utcnow().isoformat()
        )
    
    @on(Action.start_transaction)
    async def on_start_transaction(self, **kwargs):
        logger.info(f"CSMS: Received StartTransaction: {kwargs}")
        return call_result.StartTransaction(
            transaction_id=123,
            id_tag_info={"status": AuthorizationStatus.accepted}
        )

    @on(Action.stop_transaction)
    async def on_stop_transaction(self, **kwargs):
        logger.info(f"CSMS: Received StopTransaction: {kwargs}")
        return call_result.StopTransaction(
            id_tag_info={"status": AuthorizationStatus.accepted}
        )

    @on(Action.meter_values)
    async def on_meter_values(self, **kwargs):
        logger.info(f"CSMS: Received MeterValues: {kwargs}")
        return call_result.MeterValues()

    @on(Action.authorize)
    async def on_authorize(self, **kwargs):
        logger.info(f"CSMS: Received Authorize: {kwargs}")
        return call_result.Authorize(
             id_tag_info={"status": AuthorizationStatus.accepted}
        )
    
    @on(Action.data_transfer)
    async def on_data_transfer(self, **kwargs):
         logger.info(f"CSMS: Received DataTransfer: {kwargs}")
         return call_result.DataTransfer(
             status=DataTransferStatus.accepted
         )
         
    @on(Action.firmware_status_notification)
    async def on_firmware_status(self, **kwargs):
         logger.info(f"CSMS: Received FirmwareStatusNotification: {kwargs}")
         return call_result.FirmwareStatusNotification()
         
    @on(Action.diagnostics_status_notification)
    async def on_diagnostics_status(self, **kwargs):
         logger.info(f"CSMS: Received DiagnosticsStatusNotification: {kwargs}")
         return call_result.DiagnosticsStatusNotification()


async def on_connect(websocket):
    # In websockets 14+, the handler receives only the connection.
    # The path is available at websocket.request.path
    try:
        path = websocket.request.path
    except AttributeError:
        # Fallback for older websockets if needed, but we installed latest.
        # However, checking if it was passed as second arg is handled by signature.
        path = "/"
    
    charge_point_id = path.strip('/')
    cp = MockCSMS(charge_point_id, websocket)
    await cp.on_connect()
    await cp.start()

class MockChargerHardware:
    def __init__(self):
        self.stopped = True
    def stop(self):
        self.stopped = True
        logger.info("HARDWARE: Charger stopped.")
    def start(self):
        self.stopped = False
        logger.info("HARDWARE: Charger started.")
    def start_session(self):
        logger.info("HARDWARE: Charger started session.")

# --- Verification Helpers ---
async def verify_step(name, coro, expected_status="Accepted"):
    logger.info(f"TEST STEP: {name}")
    try:
        res = await coro
        if hasattr(res, 'status'):
            status = res.status
        elif isinstance(res, dict) and 'status' in res:
            status = res['status']
        else:
            status = "Unknown"
        
        if expected_status and status != expected_status:
             logger.error(f"FAIL: {name} - Expected {expected_status}, got {status}")
             return False
        logger.info(f"PASS: {name}")
        return True
    except Exception as e:
        logger.error(f"FAIL: {name} - Exception: {e}")
        return False

async def main():
    # 1. Start Server
    server = await websockets.serve(on_connect, 'localhost', 9000, subprotocols=['ocpp1.6'])
    logger.info("Mock CSMS started on localhost:9000")

    # 2. Start Client
    mock_hw = MockChargerHardware()
    async with websockets.connect(
        'ws://localhost:9000/CP_TEST',
        subprotocols=['ocpp1.6']
    ) as ws:
        cp = Ocpp16Interface("CP_TEST", ws, mock_hw)
        client_task = asyncio.create_task(cp.start())
        
        # Wait for connection
        await asyncio.wait_for(server_ready.wait(), timeout=2)
        
        # --- TEST SUITE ---
        
        # 1. Configuration Verification
        logger.info("\n--- 1. Configuration Verification ---")
        await cp.send_boot_notification()
        await cp.send_status_notification(status="Available")
        
        # 1.3 CSMS -> CS: TriggerMessage (BootNotification)
        
        req = call.TriggerMessage(requested_message="BootNotification")
        res = await connected_csms.call(req)
        logger.info(f"1.3 Trigger BootNotification: {res.status}")
        
        # 1.5 Trigger Status
        res = await connected_csms.call(call.TriggerMessage(requested_message="StatusNotification"))
        logger.info(f"1.5 Trigger Status: {res.status}")
        
        # 1.7 Trigger MeterValues
        res = await connected_csms.call(call.TriggerMessage(requested_message="MeterValues"))
        logger.info(f"1.7 Trigger MeterValues: {res.status}")
        
        # 1.10 Change Config Heartbeat
        res = await connected_csms.call(call.ChangeConfiguration(key="HeartbeatInterval", value="600"))
        logger.info(f"1.10 Change Heartbeat: {res.status}")
        
        # 1.11 Change Config MeterValue
        res = await connected_csms.call(call.ChangeConfiguration(key="MeterValueSampleInterval", value="30"))
        logger.info(f"1.11 Change MeterValue: {res.status}")
        
        # 1.12 UnlockConnector
        res = await connected_csms.call(call.UnlockConnector(connector_id=1))
        # OcppInterface needs on_unlock_connector implemented.
        logger.info(f"1.12 UnlockConnector: {res.status}")
        
        # 1.13 ChangeAvailability Inoperative
        res = await connected_csms.call(call.ChangeAvailability(connector_id=1, type="Inoperative"))
        logger.info(f"1.13 ChangeAvailability Inoperative: {res.status}")
        
        # 1.15 ChangeAvailability Operative
        res = await connected_csms.call(call.ChangeAvailability(connector_id=1, type="Operative"))
        logger.info(f"1.15 ChangeAvailability Operative: {res.status}")
        
        # 1.17 CSMS -> CS: GetDiagnostics
        res = await connected_csms.call(call.GetDiagnostics(location="ftp://example.com"))
        logger.info(f"1.17 GetDiagnostics: {res.file_name}")

        # 1.19 CSMS -> CS: UpdateFirmware
        res = await connected_csms.call(call.UpdateFirmware(location="ftp://firmware.bin", retrieve_date=datetime.utcnow().isoformat()))
        logger.info(f"1.19 UpdateFirmware: {res}")
        
        # 1.21 LocalAuthOffline: True
        res = await connected_csms.call(call.ChangeConfiguration(key="LocalAuthorizeOffline", value="True"))
        logger.info(f"1.21 LocalAuthOffline: {res.status}")
        
        # 1.22 SendLocalList
        res = await connected_csms.call(call.SendLocalList(list_version=1, local_authorization_list=[], update_type="Full"))
        logger.info(f"1.22 SendLocalList: {res.status}")
        
        # 1.23 GetLocalListVersion
        res = await connected_csms.call(call.GetLocalListVersion())
        logger.info(f"1.23 GetLocalListVersion: {res.list_version}")
        
        # 1.24 ClearCache
        res = await connected_csms.call(call.ClearCache())
        logger.info(f"1.24 ClearCache: {res.status}")
        
        
        # 2. Auto Charge Verification
        logger.info("\n--- 2. Auto Charge Verification ---")
        # 2.1 Disable AutoCharge
        res = await connected_csms.call(call.ChangeConfiguration(key="AutoCharge", value="False"))
        logger.info(f"2.1 Disable AutoCharge: {res.status}")
        
        # 2.4 Enable AutoCharge
        res = await connected_csms.call(call.ChangeConfiguration(key="AutoCharge", value="True"))
        logger.info(f"2.4 Enable AutoCharge: {res.status}")
        
        # 2.10 RemoteStop
        res = await connected_csms.call(call.RemoteStopTransaction(transaction_id=999))
        logger.info(f"2.10 RemoteStopTransaction: {res.status}")
        
        
        # 3. Normal Operation Verification
        logger.info("\n--- 3. Normal Operation Verification ---")
        # 3.12 RemoteStartTransaction
        res = await connected_csms.call(call.RemoteStartTransaction(id_tag="DEADBEEF"))
        logger.info(f"3.12 RemoteStartTransaction: {res.status}")
        
        # 3.16 RemoteStopTransaction
        res = await connected_csms.call(call.RemoteStopTransaction(transaction_id=123))
        logger.info(f"3.16 RemoteStopTransaction: {res.status}")
        
        
        # 4. Reset Verification
        logger.info("\n--- 4. Reset Verification ---")
        # 4.1 Hard Reset
        res = await connected_csms.call(call.Reset(type="Hard"))
        logger.info(f"4.1 Hard Reset: {res.status}")

        # 4.9 Soft Reset
        res = await connected_csms.call(call.Reset(type="Soft"))
        logger.info(f"4.9 Soft Reset: {res.status}")
        
        
        # 5. Reservation Verification
        logger.info("\n--- 5. Reservation Verification ---")
        # 5.2 ReserveNow
        res = await connected_csms.call(call.ReserveNow(
            reservation_id=1, 
            expiry_date=datetime.utcnow().isoformat(), 
            id_tag="RES_TAG", 
            connector_id=1
        ))
        logger.info(f"5.2 ReserveNow: {res.status}")

        # 5.4 CancelReservation
        res = await connected_csms.call(call.CancelReservation(reservation_id=1))
        logger.info(f"5.4 CancelReservation: {res.status}")
        
        
        # 6. Charging Profile Verification
        logger.info("\n--- 6. Charging Profile Verification ---")
        # 6.7 SetChargingProfile
        profile = {
            "chargingProfileId": 1,
            "stackLevel": 1,
            "chargingProfilePurpose": "TxProfile",
            "chargingProfileKind": "Absolute",
            "chargingSchedule": {
                "chargingRateUnit": "A",
                "chargingSchedulePeriod": [{"startPeriod": 0, "limit": 10.0}]
            }
        }
        res = await connected_csms.call(call.SetChargingProfile(connector_id=1, cs_charging_profiles=profile))
        logger.info(f"6.7 SetChargingProfile: {res.status}")
        
        # 6.9 Set (Update)
        profile["chargingSchedule"]["chargingSchedulePeriod"][0]["limit"] = 16.0
        res = await connected_csms.call(call.SetChargingProfile(connector_id=1, cs_charging_profiles=profile))
        logger.info(f"6.9 SetChargingProfile (Update): {res.status}")
        
        
        # 7. Abnormal Operation Verification
        logger.info("\n--- 7. Abnormal Operation Verification ---")
        # 7.1 Remote Start (Busy/Running)
        # Simulate charger is busy
        mock_hw.stopped = False
        res = await connected_csms.call(call.RemoteStartTransaction(id_tag="DEADBEEF"))
        logger.info(f"7.1 RemoteStart (Busy): {res.status}") # Expected Rejected
        mock_hw.stopped = True # Reset
        
        # 7.4 Emergency Stop (RemoteStop)
        mock_hw.stopped = False # Imagine it's running
        res = await connected_csms.call(call.RemoteStopTransaction(transaction_id=999))
        logger.info(f"7.4 Emergency Stop: {res.status}")
        mock_hw.stopped = True
        
        
        # 9. MEA Specific Configuration
        logger.info("\n--- 9. MEA Specific Configuration ---")
        # 9.1 Heartbeat 3600
        res = await connected_csms.call(call.ChangeConfiguration(key="HeartbeatInterval", value="3600"))
        logger.info(f"9.1 Heartbeat 3600: {res.status}")
        
        # 9.2 MeterValueSampleInterval 60
        res = await connected_csms.call(call.ChangeConfiguration(key="MeterValueSampleInterval", value="60"))
        logger.info(f"9.2 MeterValueSampleInterval 60: {res.status}")
        
        # 9.4 V2G Workaround
        res = await connected_csms.call(call.ChangeConfiguration(key="MEA_V2G_PowerDemand", value="5000"))
        logger.info(f"9.4 V2G PowerDemand 5000: {res.status}")
        
        res = await connected_csms.call(call.ChangeConfiguration(key="MEA_V2G_PowerDemand", value="-2000"))
        logger.info(f"9.4 V2G PowerDemand -2000: {res.status}")


        logger.info("Test Suite Completed.")
        
        # Cleanup
        client_task.cancel()
        server.close()
        await server.wait_closed()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass

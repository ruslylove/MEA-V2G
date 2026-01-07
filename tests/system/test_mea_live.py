import asyncio
import websockets
import sys
import os
import logging
import requests
from requests.auth import HTTPDigestAuth
from datetime import datetime, timedelta

# Add parent directory to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from Ocpp16Interface import Ocpp16Interface
from ocpp.v16 import call

# --- Log Formatter ---
class LogFormatter(logging.Formatter):
    def format(self, record):
        return record.getMessage()

# Configure logging
logger = logging.getLogger('LiveTest')
logger.setLevel(logging.INFO)
logger.propagate = False # Prevent double logging
if not logger.handlers:
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(LogFormatter())
    logger.addHandler(ch)

CP_ID = "rddQC4000001"
CSMS_URL = f"wss://ocpp.measandbox.com:2930/EV/Srv/JSON/1.6/{CP_ID}"
API_BASE_URL = "https://ocppapi.measandbox.com/EV"

class MeaApi:
    def __init__(self):
        self.username = "meaev.api.dev"
        self.pwd_remote_start = "Bh9GKYvSBc9KkbJ"
        self.pwd_default = "U`?d3~C_Se77CrdsG[l#hq1)J_2$FA1D"

    def _post(self, path, payload, password=None):
        url = f"{API_BASE_URL}{path}"
        pwd = password if password else self.pwd_default
        auth = HTTPDigestAuth(self.username, pwd)
        
        # logger.info(f"API Request: POST {url}") 
        # Reduced noise for test clarity, only log errors or specific steps
        try:
            resp = requests.post(url, json=payload, auth=auth, timeout=10)
            return resp
        except Exception as e:
            logger.error(f"API Request failed: {e}")
            return None

    def remote_start(self, cp_id, connector_id=1, card_id="RFID_SIM"):
        path = "/cmd/chargepoint/remoteStart"
        payload = {
            "chargepoint_id": cp_id,
            "connector_id": connector_id,
            "card_id": card_id
        }
        return self._post(path, payload, password=self.pwd_remote_start)

    def remote_stop(self, cp_id, transaction_id):
        path = "/cmd/chargepoint/remoteStop"
        payload = {
            "chargepoint_id": cp_id,
            "transaction_id": transaction_id
        }
        return self._post(path, payload, password=self.pwd_default)

    def reserve(self, cp_id, connector=1, duration=15, card_id="0FABD4C1234"):
        path = "/cmd/chargepoint/reserve"
        payload = {
            "chargepoint": cp_id,
            "connector": connector,
            "duration": duration,
            "card_id": card_id
        }
        return self._post(path, payload, password=self.pwd_default)

    def cancel_reservation(self, cp_id, reservation_id):
        path = "/cmd/chargepoint/cancel"
        payload = {
            "chargepoint": cp_id,
            "reservation_id": reservation_id
        }
        return self._post(path, payload, password=self.pwd_default)

    def change_configuration(self, cp_id, key, value):
        path = "/remote/changeConfiguration"
        payload = {
            "chargepoint_id": cp_id,
            "key": key,
            "value": value
        }
        return self._post(path, payload, password=self.pwd_default)

    def get_configuration(self, cp_id, key=None):
        path = "/cmd/chargepoint/getConfiguration"
        payload = {
            "chargepoint_id": cp_id,
            "key": [key] if key else []
        }
        return self._post(path, payload, password=self.pwd_default)

    def reset(self, cp_id, type="Soft"):
        path = "/remote/reset"
        payload = {
            "chargepoint": cp_id,
            "reset_type": type
        }
        return self._post(path, payload, password=self.pwd_default)

    def set_charging_profile(self, cp_id, connector_id, profile):
        path = "/remote/SetChargingProfile"
        payload = {
            "chargepoint_id": cp_id,
            "connectorId": connector_id,
            "csChargingProfiles": profile
        }
        return self._post(path, payload, password=self.pwd_default)

    def trigger_message(self, cp_id, message, connector_id=1):
        path = "/remote/triggerMessage"
        payload = {
            "chargepoint_id": cp_id,
            "connectorId": connector_id,
            "requestedMessage": message
        }
        return self._post(path, payload, password=self.pwd_default)

    def unlock_connector(self, cp_id, connector_id=1):
        path = "/remote/unlockConnector"
        payload = {
            "chargepoint_id": cp_id,
            "connectorId": connector_id
        }
        return self._post(path, payload, password=self.pwd_default)

class MockCharger:
    def __init__(self):
        self.stopped = True
    def stop(self):
        self.stopped = True
    def start(self):
        self.stopped = False
    def start_session(self):
        pass
    def getEvsePresentVoltage(self):
        return 230.0
    def getEvsePresentCurrent(self):
        return 0.0 if self.stopped else 10.0

async def main():
    logger.info(f"TEST STEP: Connecting to {CSMS_URL}...")
    api = MeaApi()

    try:
        async with websockets.connect(
            CSMS_URL,
            subprotocols=['ocpp1.6'],
            ping_interval=None
        ) as ws:
            logger.info("PASS: WebSocket Connected")
            
            charger_mock = MockCharger()
            cp = Ocpp16Interface(CP_ID, ws, charger_mock)
            
            # Start CP loop
            loop_task = asyncio.create_task(cp.start())
            await asyncio.sleep(1)

            # --- 1. Boot Verification ---
            logger.info("\n--- 1. Boot Verification ---")

            # 1.1 BootNotification
            await cp.send_boot_notification()
            logger.info("1.1 BootNotification: Sent")

            # 1.2 StatusNotification (Available)
            try:
                await cp.send_status_notification(status="Available")
                logger.info("1.2 StatusNotification: Available")
            except Exception as e:
                logger.warning(f"WARN: 1.2 StatusNotification: Timeout/Error. Details: {str(e)[:50]}...")

            # 1.3 Heartbeat
            await cp.call(call.Heartbeat())
            logger.info("1.3 Heartbeat: Sent")


            # --- 2. Configuration Verification ---
            logger.info("\n--- 2. Configuration Verification ---")
            
            # 2.1 GetConfiguration (HeartbeatInterval)
            resp = await asyncio.to_thread(api.get_configuration, CP_ID, "HeartbeatInterval")
            if resp and resp.status_code == 200:
                logger.info(f"2.1 GetConfiguration (HeartbeatInterval): Sent (Status {resp.status_code})")
            else:
                 logger.error(f"FAIL: 2.1 GetConfiguration (HeartbeatInterval): Failed (Status {resp.status_code if resp else 'None'})")

            # 2.2 ChangeConfiguration (MEA_V2G_PowerDemand) - Charge (Import)
            resp = await asyncio.to_thread(api.change_configuration, CP_ID, "MEA_V2G_PowerDemand", "2000")
            logger.info(f"2.2 ChangeConfiguration (MEA_V2G_PowerDemand 2000): Sent (Status {resp.status_code})")
            await asyncio.sleep(1) 
            
            # 2.3 ChangeConfiguration (MEA_V2G_PowerDemand) - Discharge (Export)
            resp = await asyncio.to_thread(api.change_configuration, CP_ID, "MEA_V2G_PowerDemand", "-2000")
            logger.info(f"2.3 ChangeConfiguration (MEA_V2G_PowerDemand -2000): Sent (Status {resp.status_code})")
            await asyncio.sleep(1)


            # --- 3. Remote Control Verification ---
            logger.info("\n--- 3. Remote Control Verification ---")

            # 3.1 RemoteStartTransaction
            resp = await asyncio.to_thread(api.remote_start, CP_ID, 1, "LIVE_TEST_TAG")
            logger.info(f"3.1 RemoteStartTransaction: Sent (Status {resp.status_code})")
            
            await asyncio.sleep(3) # Wait for StartTx
            
            tx_id = cp.transactions.get(1)
            if tx_id:
                logger.info(f"3.2 StartTransaction: Started (ID {tx_id})")
                await cp.send_status_notification(status="Charging")
                logger.info("3.3 StatusNotification: Charging")
                
                # 3.4 RemoteStopTransaction
                resp = await asyncio.to_thread(api.remote_stop, CP_ID, tx_id)
                logger.info(f"3.4 RemoteStopTransaction: Sent (Status {resp.status_code})")
                
                await asyncio.sleep(3)
                if 1 not in cp.transactions:
                    logger.info("3.5 StopTransaction: Stopped")
                    await cp.send_status_notification(status="Finishing")
                    logger.info("3.6 StatusNotification: Finishing")
                    await cp.send_status_notification(status="Available")
                    logger.info("3.7 StatusNotification: Available")
                else:
                    logger.error("FAIL: 3.5 StopTransaction: Transaction NOT Stopped")
            else:
                logger.error("FAIL: 3.2 StartTransaction: Transaction did NOT start")


            # --- 4. Reservation Verification ---
            logger.info("\n--- 4. Reservation Verification ---")
            
            # 4.1 ReserveNow
            resp = await asyncio.to_thread(api.reserve, CP_ID, 1, 15, "RES_TAG")
            logger.info(f"4.1 ReserveNow: Sent (Status {resp.status_code})")
            await asyncio.sleep(1)
            
            # 4.2 CancelReservation
            # Using dummy reservation ID 1 as placeholder since we can't easily parse it from the previous void response in simple HTTP check
            resp = await asyncio.to_thread(api.cancel_reservation, CP_ID, 1)
            logger.info(f"4.2 CancelReservation: Sent (Status {resp.status_code})")


            # --- 5. Smart Charging Verification ---
            logger.info("\n--- 5. Smart Charging Verification ---")
            
            # 5.1 SetChargingProfile
            valid_from = datetime.utcnow().isoformat() + "Z"
            valid_to = (datetime.utcnow() + timedelta(days=1)).isoformat() + "Z"
            start_schedule = datetime.utcnow().isoformat() + "Z"
            
            profile = {
                "chargingProfileId": 101,
                "stackLevel": 1,
                "chargingProfilePurpose": "TxDefaultProfile",
                "chargingProfileKind": "Absolute",
                "recurrencyKind": "Daily",
                "validFrom": valid_from,
                "validTo": valid_to,
                "chargingSchedule": {
                    "duration": 600,
                    "startSchedule": start_schedule,
                    "chargingRateUnit": "A",
                    "minChargingRate": 6,
                    "chargingSchedulePeriod": [{
                        "startPeriod": 0,
                        "limit": 16.0,
                        "numberPhases": 3
                    }]
                }
            }
            resp = await asyncio.to_thread(api.set_charging_profile, CP_ID, 1, profile)
            logger.info(f"5.1 SetChargingProfile: Sent (Status {resp.status_code})")
            await asyncio.sleep(1)


            # --- 6. Reset Verification ---
            logger.info("\n--- 6. Reset Verification ---")
            
            # 6.1 Reset (Soft)
            resp = await asyncio.to_thread(api.reset, CP_ID, "Soft")
            logger.info(f"6.1 Reset (Soft): Sent (Status {resp.status_code})")
            
            
            # --- 7. Trigger Verification ---
            logger.info("\n--- 7. Trigger Verification ---")
            
            # 7.1 TriggerMessage (Heartbeat)
            resp = await asyncio.to_thread(api.trigger_message, CP_ID, "Heartbeat")
            if resp and resp.status_code == 200:
                logger.info(f"7.1 TriggerMessage (Heartbeat): Sent (Status {resp.status_code})")
            else:
                logger.warning(f"WARN: 7.1 TriggerMessage (Heartbeat): Failed (Status {resp.status_code if resp else 'None'})")

            # 7.2 TriggerMessage (MeterValues)
            resp = await asyncio.to_thread(api.trigger_message, CP_ID, "MeterValues")
            if resp and resp.status_code == 200:
                logger.info(f"7.2 TriggerMessage (MeterValues): Sent (Status {resp.status_code})")
            else:
                logger.warning(f"WARN: 7.2 TriggerMessage (MeterValues): Failed (Status {resp.status_code if resp else 'None'})")
            await asyncio.sleep(1) 


            # --- 8. Unlock Verification ---
            logger.info("\n--- 8. Unlock Verification ---")
            
            # 8.1 UnlockConnector
            resp = await asyncio.to_thread(api.unlock_connector, CP_ID, 1)
            if resp and resp.status_code == 200:
                logger.info(f"8.1 UnlockConnector: Sent (Status {resp.status_code})")
            else:
                logger.warning(f"WARN: 8.1 UnlockConnector: Failed (Status {resp.status_code if resp else 'None'})")
            
            
            logger.info("\nTEST SUITE COMPLETED.")
            
            loop_task.cancel()
            try:
                await loop_task
            except asyncio.CancelledError:
                pass

    except Exception as e:
        logger.error(f"Test Exception: {e}")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass

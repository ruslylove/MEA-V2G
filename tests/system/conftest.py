import pytest
import asyncio
import websockets
import os
import sys
import logging
from requests.auth import HTTPDigestAuth
import requests

# Add parent directory to path to import MeaApi from test_mea_live or define it here
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from Ocpp16Interface import Ocpp16Interface
import ssl

# Disable SSL verification for sandbox environment if needed (common on macOS)
ssl_context = ssl.create_default_context()
ssl_context.check_hostname = False
ssl_context.verify_mode = ssl.CERT_NONE

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
        try:
            resp = requests.post(url, json=payload, auth=auth, timeout=10)
            return resp
        except Exception as e:
            print(f"API Request failed: {e}")
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

@pytest.fixture
def mea_api():
    return MeaApi()

@pytest.fixture
async def live_evse():
    async with websockets.connect(
        CSMS_URL,
        subprotocols=['ocpp1.6'],
        ping_interval=None,
        ssl=ssl_context
    ) as ws:
        charger_mock = MockCharger()
        cp = Ocpp16Interface(CP_ID, ws, charger_mock)
        
        # Start CP loop
        loop_task = asyncio.create_task(cp.start())
        await asyncio.sleep(0.5)
        
        yield cp
        
        loop_task.cancel()
        try:
            await loop_task
        except asyncio.CancelledError:
            pass

import pytest
import time
import json
import datetime
import logging
import requests
from tests.api.test_mea_postman import PreemptiveDigestAuth, BASE_URL, USERNAME, PASSWORD, log_api_interaction
from Ocpp16Interface import PACKET_QUEUE, ChargePointStatus, Ocpp16Interface
import queue

# Re-use config
CHARGEPOINT_ID = "rddQC4000001"
VID_TAG = "RFID_SIM"

@pytest.fixture(scope="module")
def api_auth():
    return PreemptiveDigestAuth(USERNAME, PASSWORD, "MEASandBox")

def send_sim_command(sim, command, **kwargs):
    """Helper to send commands to EVSE Simulation"""
    cmd = {'command': command, 'args': kwargs}
    sim.queue.put(cmd)
    time.sleep(1)

def wait_for_packet(expected_substring, timeout=10):
    """Consumes packets looking for substring"""
    start = time.time()
    while time.time() - start < timeout:
        try:
            msg = PACKET_QUEUE.get(timeout=0.2)
            if expected_substring in msg:
                 return True
        except queue.Empty:
            continue
    return False

@pytest.mark.usefixtures("evse_simulation", "api_auth")
class TestMeaSection6:
    """
    Test Case 6: Charging Profile Verification.
    Sequence 6.1 - 6.26.
    """
    @classmethod
    def setup_class(cls):
        # Enable extended logging for this section report
        Ocpp16Interface.LOG_SEND_PACKETS = True
        print("\n[TestMeaSection6] Extended Logging ENABLED for Report Generation")

    @classmethod
    def teardown_class(cls):
        # Restore default
        Ocpp16Interface.LOG_SEND_PACKETS = False
        print("\n[TestMeaSection6] Extended Logging DISABLED")

    # State
    transaction_id_1 = None
    transaction_id_2 = None
    packet_buffer = []

    def wait_for_packet(self, expected_substring, timeout=10):
        """Consumes packets looking for substring, buffering others"""
        # Check buffer first
        for i, msg in enumerate(self.packet_buffer):
            if expected_substring in msg:
                print(f"DEBUG: Found '{expected_substring}' in buffer")
                self.packet_buffer.pop(i)
                return True
        
        start = time.time()
        while time.time() - start < timeout:
            try:
                msg = PACKET_QUEUE.get(timeout=0.2)
                if expected_substring in msg:
                    return True
                else:
                    self.packet_buffer.append(msg)
            except queue.Empty:
                continue
        return False
    
    # --- Flow 1: Profile during Local Start ---
    
    def test_6_01_status_available(self, evse_simulation):
        print("\n--- 6.1 StatusNotification (Available) ---")
        evse_simulation.queue.put({'command': 'STATUS', 'args': {'connector_id': 1, 'status': 'Available'}})
        assert self.wait_for_packet("StatusNotification ACKNOWLEDGED (Available)", timeout=10)

    def test_6_02_status_preparing(self, evse_simulation):
        print("\n--- 6.2 StatusNotification (Preparing) ---")
        evse_simulation.queue.put({'command': 'STATUS', 'args': {'connector_id': 1, 'status': 'Preparing'}})
        assert self.wait_for_packet("StatusNotification ACKNOWLEDGED (Preparing)", timeout=10)

    def test_6_03_authorize(self, evse_simulation):
        print("\n--- 6.3 Authorize ---")
        evse_simulation.queue.put({'command': 'AUTHORIZE', 'args': {'id_tag': VID_TAG}})
        assert self.wait_for_packet("Authorize ACKNOWLEDGED (Accepted)", timeout=10)

    def test_6_04_start_transaction(self, evse_simulation):
        print("\n--- 6.4 StartTransaction ---")
        # Sim sends StartTransaction
        evse_simulation.queue.put({'command': 'START_TRANSACTION', 'args': {'connector_id': 1, 'id_tag': VID_TAG}})
        assert self.wait_for_packet("Transaction started on 1", timeout=15)
        
    def test_6_05_status_charging(self, evse_simulation):
        print("\n--- 6.5 StatusNotification (Charging) ---")
        # Auto-sent by Sim on StartTx
        assert self.wait_for_packet("StatusNotification ACKNOWLEDGED (Charging)", timeout=10)

    def test_6_06_meter_values(self, evse_simulation):
        print("\n--- 6.6 MeterValues ---")
        evse_simulation.queue.put({'command': 'METER_VALUES', 'args': {'connector_id': 1}})
        time.sleep(2)
        
    def test_6_07_set_charging_profile_1(self, evse_simulation, api_auth):
        print("\n--- 6.7 SetChargingProfile (TxProfile) ---")
        url = f"{BASE_URL}/EV/remote/SetChargingProfile"
        
        # Determine current TID from sim state
        tid = 0
        if evse_simulation.cp and 1 in evse_simulation.cp.transactions:
            tid = evse_simulation.cp.transactions[1]
        
        now = datetime.datetime.now(datetime.timezone.utc)
        valid_to = (now + datetime.timedelta(days=1)).isoformat().replace("+00:00", "Z")
        start_schedule = (now + datetime.timedelta(minutes=1)).isoformat().replace("+00:00", "Z")

        payload = {
            "chargepoint_id": CHARGEPOINT_ID,
            "connectorId": 1,
            "csChargingProfiles": {
                "chargingProfileId": 101,
                "transactionId": tid,
                "stackLevel": 1,
                "chargingProfilePurpose": "TxProfile",
                "chargingProfileKind": "Absolute",
                "recurrencyKind": "Daily",
                "validFrom": (now - datetime.timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
                "validTo": valid_to,
                "chargingSchedule": {
                    "duration": 3600,
                    "startSchedule": start_schedule,
                    "chargingRateUnit": "W", # Using W for Power Profile
                    "chargingSchedulePeriod": [{
                        "startPeriod": 0,
                        "limit": 5000, # 5kW
                        "numberPhases": 3
                    }]
                }
            }
        }
        
        resp = requests.post(url, json=payload, auth=api_auth)
        print(f"SetChargingProfile Resp: {resp.text}")
        assert resp.status_code == 200
        assert self.wait_for_packet("SetChargingProfile Response (Accepted)", timeout=10)

    def test_6_08_meter_values(self, evse_simulation):
        print("\n--- 6.8 MeterValues ---")
        evse_simulation.queue.put({'command': 'METER_VALUES', 'args': {'connector_id': 1}})
        time.sleep(2)

    def test_6_09_set_charging_profile_2(self, evse_simulation, api_auth):
        print("\n--- 6.9 SetChargingProfile (Update/New) ---")
        self.test_6_07_set_charging_profile_1(evse_simulation, api_auth)

    def test_6_10_meter_values(self, evse_simulation):
        print("\n--- 6.10 MeterValues ---")
        evse_simulation.queue.put({'command': 'METER_VALUES', 'args': {'connector_id': 1}})
        time.sleep(2)

    def test_6_11_stop_transaction(self, evse_simulation):
        print("\n--- 6.11 StopTransaction ---")
        evse_simulation.queue.put({'command': 'STOP_TRANSACTION', 'args': {'connector_id': 1}})
        assert self.wait_for_packet("Transaction stopped", timeout=10)

    def test_6_12_status_finishing(self, evse_simulation):
        print("\n--- 6.12 StatusNotification (Finishing) ---")
        assert self.wait_for_packet("StatusNotification ACKNOWLEDGED (Finishing)", timeout=10)

    def test_6_13_status_available(self, evse_simulation):
        print("\n--- 6.13 StatusNotification (Available) ---")
        assert self.wait_for_packet("StatusNotification ACKNOWLEDGED (Available)", timeout=10)

    # --- Flow 2: Profile during Remote Start ---

    def test_6_14_status_preparing(self, evse_simulation):
        print("\n--- 6.14 StatusNotification (Preparing) ---")
        evse_simulation.queue.put({'command': 'STATUS', 'args': {'connector_id': 1, 'status': 'Preparing'}})
        assert self.wait_for_packet("StatusNotification ACKNOWLEDGED (Preparing)", timeout=10)

    def test_6_15_remote_start_transaction(self, evse_simulation, api_auth):
        print("\n--- 6.15 RemoteStartTransaction ---")
        url = f"{BASE_URL}/EV/cmd/chargepoint/remoteStart"
        payload = {
            "chargepoint_id": CHARGEPOINT_ID,
            "connector_id": 1,
            "card_id": VID_TAG
        }
        PWD_REMOTE = "Bh9GKYvSBc9KkbJ"
        auth_remote = PreemptiveDigestAuth(USERNAME, PWD_REMOTE, realm="EV")
        
        resp = requests.post(url, json=payload, auth=auth_remote)
        assert resp.status_code == 200
        assert self.wait_for_packet("RemoteStartTransaction Response (Accepted)", timeout=10)

    def test_6_16_start_transaction(self, evse_simulation):
        print("\n--- 6.16 StartTransaction ---")
        # Auto triggered by RemoteStart in Sim
        assert self.wait_for_packet("Transaction started on 1", timeout=10)

    def test_6_17_status_charging(self, evse_simulation):
        print("\n--- 6.17 StatusNotification (Charging) ---")
        assert self.wait_for_packet("StatusNotification ACKNOWLEDGED (Charging)", timeout=10)

    def test_6_18_meter_values(self, evse_simulation):
        print("\n--- 6.18 MeterValues ---")
        evse_simulation.queue.put({'command': 'METER_VALUES', 'args': {'connector_id': 1}})
        time.sleep(2)

    def test_6_19_set_charging_profile_3(self, evse_simulation, api_auth):
        print("\n--- 6.19 SetChargingProfile ---")
        self.test_6_07_set_charging_profile_1(evse_simulation, api_auth)

    def test_6_20_meter_values(self, evse_simulation):
        print("\n--- 6.20 MeterValues ---")
        evse_simulation.queue.put({'command': 'METER_VALUES', 'args': {'connector_id': 1}})
        time.sleep(2)

    def test_6_21_set_charging_profile_4(self, evse_simulation, api_auth):
        print("\n--- 6.21 SetChargingProfile ---")
        self.test_6_07_set_charging_profile_1(evse_simulation, api_auth)

    def test_6_22_meter_values(self, evse_simulation):
        print("\n--- 6.22 MeterValues ---")
        evse_simulation.queue.put({'command': 'METER_VALUES', 'args': {'connector_id': 1}})
        time.sleep(2)

    def test_6_23_remote_stop_transaction(self, evse_simulation, api_auth):
        print("\n--- 6.23 RemoteStopTransaction ---")
        
        # Get active TID
        tid = 0
        if evse_simulation.cp and 1 in evse_simulation.cp.transactions:
             tid = evse_simulation.cp.transactions[1]
             
        url = f"{BASE_URL}/EV/cmd/chargepoint/remoteStop"
        payload = {
            "chargepoint_id": CHARGEPOINT_ID,
            "transaction_id": tid
        }
        resp = requests.post(url, json=payload, auth=api_auth)
        assert resp.status_code == 200
        assert self.wait_for_packet("RemoteStopTransaction Response (Accepted)", timeout=10)

    def test_6_24_stop_transaction(self, evse_simulation):
        print("\n--- 6.24 StopTransaction ---")
        assert self.wait_for_packet("Transaction stopped", timeout=10)

    def test_6_25_status_finishing(self, evse_simulation):
        print("\n--- 6.25 StatusNotification (Finishing) ---")
        assert self.wait_for_packet("StatusNotification ACKNOWLEDGED (Finishing)", timeout=10)

    def test_6_26_status_available(self, evse_simulation):
        print("\n--- 6.26 StatusNotification (Available) ---")
        assert self.wait_for_packet("StatusNotification ACKNOWLEDGED (Available)", timeout=10)

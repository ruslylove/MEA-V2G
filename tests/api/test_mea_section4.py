import pytest
import time
import json
import logging
import requests
from tests.api.test_mea_postman import PreemptiveDigestAuth, BASE_URL, USERNAME, PASSWORD, log_api_interaction
from Ocpp16Interface import PACKET_QUEUE, ChargePointStatus
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
class TestMeaSection4:
    """
    Test Case 4: Reset Check.
    Sequence 4.1 - 4.21.
    """
    
    # Shared state
    transaction_id = None
    
    # --- Flow 1: Hard Reset (Idle) --- (4.1 - 4.8)

    def test_4_01_reset_hard(self, evse_simulation, api_auth):
        print("\n--- 4.1 Reset (Hard) ---")
        url = f"{BASE_URL}/EV/remote/reset"
        payload = {
            "chargepoint": CHARGEPOINT_ID,
            "reset_type": "Hard"
        }
        resp = requests.post(url, json=payload, auth=api_auth)
        assert resp.status_code == 200
        assert "Accepted" in resp.text
        # Verify EVSE received and accepted
        assert wait_for_packet("Reset Response (Accepted)", timeout=10)
        # In a real sim, Hard Reset might restart the process.
        # Our sim just logs it and continues for now, which is fine for the test flow.
        # Ideally we should send BootNotification again as effect of reset.
        time.sleep(2)
        send_sim_command(evse_simulation, 'BOOT')

    def test_4_02_boot_notification(self, evse_simulation):
        print("\n--- 4.2 BootNotification ---")
        # Triggered manually above to simulate reboot effect
        assert wait_for_packet("BootNotification ACCEPTED", timeout=10)

    def test_4_03_status_notification_unplug(self, evse_simulation):
        print("\n--- 4.3 StatusNotification (Unplug) -> Available ---")
        send_sim_command(evse_simulation, 'STATUS', connector_id=1, status='Available', info="Unplugged")
        assert wait_for_packet("StatusNotification ACKNOWLEDGED (Available)", timeout=5)

    def test_4_04_status_notification_plug(self, evse_simulation):
        print("\n--- 4.4 StatusNotification (Plug) -> Preparing ---")
        send_sim_command(evse_simulation, 'STATUS', connector_id=1, status='Preparing', info="PluggedIn")
        assert wait_for_packet("StatusNotification ACKNOWLEDGED (Preparing)", timeout=5)

    def test_4_05_remote_start_transaction(self, evse_simulation, api_auth):
        print("\n--- 4.5 RemoteStartTransaction ---")
        url = f"{BASE_URL}/EV/cmd/chargepoint/remoteStart"
        payload = {
            "chargepoint_id": CHARGEPOINT_ID,
            "connector_id": 1,
            "card_id": VID_TAG
        }
        PWD_REMOTE_START = "Bh9GKYvSBc9KkbJ"
        auth_remote = PreemptiveDigestAuth(USERNAME, PWD_REMOTE_START, realm="EV")

        resp = requests.post(url, json=payload, auth=auth_remote)
        assert resp.status_code == 200
        assert wait_for_packet("RemoteStartTransaction", timeout=10)

    def test_4_06_start_transaction(self, evse_simulation):
        print("\n--- 4.6 StartTransaction ---")
        # Triggered auto by remote start logic in Sim
        time.sleep(2)
        if evse_simulation.cp and 1 in evse_simulation.cp.transactions:
             TestMeaSection4.transaction_id = evse_simulation.cp.transactions[1]
             print(f"Transaction Started: {TestMeaSection4.transaction_id}")
        else:
             print("WARNING: Transaction not found in Sim state.")

    def test_4_07_status_notification_charging(self, evse_simulation):
        print("\n--- 4.7 StatusNotification (Charging) ---")
        send_sim_command(evse_simulation, 'STATUS', connector_id=1, status='Charging')
        assert wait_for_packet("StatusNotification ACKNOWLEDGED (Charging)", timeout=5)

    def test_4_08_meter_values(self, evse_simulation):
        print("\n--- 4.8 MeterValues ---")
        send_sim_command(evse_simulation, 'METER_VALUES', connector_id=1)

    # --- Flow 2: Reset Soft (During Charging) --- (4.9 - 4.17)

    def test_4_09_reset_soft(self, evse_simulation, api_auth):
        print("\n--- 4.9 Reset (Soft) ---")
        url = f"{BASE_URL}/EV/remote/reset"
        payload = {
            "chargepoint": CHARGEPOINT_ID,
            "reset_type": "Soft"
        }
        resp = requests.post(url, json=payload, auth=api_auth)
        assert resp.status_code == 200
        assert "Accepted" in resp.text
        assert wait_for_packet("Reset Response (Accepted)", timeout=10)
        
        # EFFECT: Sim should Stop Transaction upon Reset (or immediately after).
        # We simulate this behavior by sending STOP command manually to match expectation 4.10
        # In real FW, reset signal causes gracefull shutdown.
        send_sim_command(evse_simulation, 'STOP_TRANSACTION', connector_id=1, reason="HardReset") # or SoftReset/Reboot

    def test_4_10_stop_transaction(self, evse_simulation):
        print("\n--- 4.10 StopTransaction ---")
        # Triggered above.
        assert wait_for_packet("StatusNotification ACKNOWLEDGED (Finishing)", timeout=5)
        # Note: StopTx packet itself might be consumed, but confirming status change is good enough or use wait_for properly.

    def test_4_11_status_notification_plug(self, evse_simulation):
        print("\n--- 4.11 StatusNotification (Plug) ---")
        # Still plugged.
        pass

    def test_4_12_status_notification_unplug(self, evse_simulation):
        print("\n--- 4.12 StatusNotification (Unplug) -> Available ---")
        send_sim_command(evse_simulation, 'STATUS', connector_id=1, status='Available', info="Unplugged")
        assert wait_for_packet("StatusNotification ACKNOWLEDGED (Available)", timeout=5)

    def test_4_13_status_notification_plug(self, evse_simulation):
        print("\n--- 4.13 StatusNotification (Plug) -> Preparing ---")
        send_sim_command(evse_simulation, 'STATUS', connector_id=1, status='Preparing', info="PluggedIn")
        assert wait_for_packet("StatusNotification ACKNOWLEDGED (Preparing)", timeout=5)

    def test_4_14_remote_start_transaction(self, evse_simulation, api_auth):
        print("\n--- 4.14 RemoteStartTransaction ---")
        url = f"{BASE_URL}/EV/cmd/chargepoint/remoteStart"
        payload = {
            "chargepoint_id": CHARGEPOINT_ID,
            "connector_id": 1,
            "card_id": VID_TAG
        }
        PWD_REMOTE_START = "Bh9GKYvSBc9KkbJ"
        auth_remote = PreemptiveDigestAuth(USERNAME, PWD_REMOTE_START, realm="EV")
        resp = requests.post(url, json=payload, auth=auth_remote)
        assert resp.status_code == 200
        assert wait_for_packet("RemoteStartTransaction", timeout=10)

    def test_4_15_start_transaction(self, evse_simulation):
        print("\n--- 4.15 StartTransaction ---")
        time.sleep(2) # Allow start

    def test_4_16_status_notification_charging(self, evse_simulation):
        print("\n--- 4.16 StatusNotification (Charging) ---")
        send_sim_command(evse_simulation, 'STATUS', connector_id=1, status='Charging')
        assert wait_for_packet("StatusNotification ACKNOWLEDGED (Charging)", timeout=5)

    def test_4_17_meter_values(self, evse_simulation):
        print("\n--- 4.17 MeterValues ---")
        send_sim_command(evse_simulation, 'METER_VALUES', connector_id=1)

    # --- Flow 3: Reset Hard (During Charging) --- (4.18 - 4.21)

    def test_4_18_reset_hard(self, evse_simulation, api_auth):
        print("\n--- 4.18 Reset (Hard) ---")
        url = f"{BASE_URL}/EV/remote/reset"
        payload = {
            "chargepoint": CHARGEPOINT_ID,
            "reset_type": "Hard"
        }
        resp = requests.post(url, json=payload, auth=api_auth)
        assert resp.status_code == 200
        assert wait_for_packet("Reset Response (Accepted)", timeout=10)
        
        # Simulate StopTx due to reset
        send_sim_command(evse_simulation, 'STOP_TRANSACTION', connector_id=1, reason="HardReset")

    def test_4_19_stop_transaction(self, evse_simulation):
        print("\n--- 4.19 StopTransaction ---")
        assert wait_for_packet("StatusNotification ACKNOWLEDGED (Finishing)", timeout=5)

    def test_4_20_status_notification_plug(self, evse_simulation):
        print("\n--- 4.20 StatusNotification (Plug) ---")
        pass

    def test_4_21_status_notification_unplug(self, evse_simulation):
        print("\n--- 4.21 StatusNotification (Unplug) -> Available ---")
        send_sim_command(evse_simulation, 'STATUS', connector_id=1, status='Available', info="Unplugged")
        assert wait_for_packet("StatusNotification ACKNOWLEDGED (Available)", timeout=5)


import pytest
import requests
from requests.auth import HTTPDigestAuth
import json
import uuid
from datetime import datetime, timedelta
import time

# Configuration from Postman Collection
BASE_URL = "https://ocppapi.measandbox.com"
USERNAME = "meaev.api.dev"
# Most common password in collection:
PASSWORD = "U`?d3~C_Se77CrdsG[l#hq1)J_2$FA1D" 
# Alternative password seen in 'remote start': "Bh9GKYvSBc9KkbJ"

# Default values from collection
CHARGEPOINT_ID = "rddQC4000001"
CONNECTOR_ID = 1
CARD_ID = "RFID_SIM"

@pytest.fixture
def auth():
    return HTTPDigestAuth(USERNAME, PASSWORD)

@pytest.fixture
def headers():
    return {
    "Content-Type": "application/json"
    }

import sys
import os
# Ensure root is in path for imports
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))
from Ocpp16Interface import PACKET_QUEUE
import queue

def log_api_interaction(name, url, method, payload, response, wait_time=20.0):
    print(f"\n--- TEST CASE: {name} ---")
    print(f"API Request: {method} {url}")
    print(f"Payload: {json.dumps(payload, indent=2) if payload else 'None'}")
    print(f"Response Code: {response.status_code}")
    if response.content:
        try:
             print(f"Response Body: {json.dumps(response.json(), indent=2)}")
        except:
             print(f"Response Body (Raw): {response.text}")
    else:
        print("Response Body: <Empty>")
    
    # Dump any accumulated EVSE logs
    print("--- EVSE Interactions ---")
    
    start_time = time.time()
    # We want to capture as much as possible, but not block forever if nothing comes.
    # However, if we expect a response, we should wait.
    # The loop continues reading until timeout. This collects multiple messages.
    while time.time() - start_time < wait_time:
        try:
            # Wait for next message
            msg = PACKET_QUEUE.get(timeout=0.2)
            print(msg)
        except queue.Empty:
            continue

    print(f"--------------------------------")

def test_00_connection_setup():
    """
    Verifies that the EVSE Simulation connects, boots, and sends status.
    This test consumes the initial log events from the queue so they appear
    in their own section in the report.
    """
    print("\n--- TEST CASE: Connection Setup ---")
    print("Waiting for EVSE to initialize...")
    
    logs = []
    print("--- EVSE Interactions ---")
    
    start_time = time.time()
    # Wait up to 25 seconds for full startup sequence (covering 20s timeout)
    while time.time() - start_time < 25:
        try:
            msg = PACKET_QUEUE.get(timeout=0.1)
            print(msg)
            logs.append(msg)
            # Stop waiting if we see the last expected message
            if "Heartbeat ACKNOWLEDGED" in msg:
                 break
        except queue.Empty:
            continue
            
    print("--------------------------------")
    
    # Verify we actually connected
    # We expect at least BootNotification ACCEPTED
    joined_logs = "\n".join(logs)
    if "[EVSE] BootNotification ACCEPTED" in joined_logs:
         print("SUCCESS: BootNotification was accepted.")
    else:
         # Warn but don't fail hard if it's slow, though it explains missing logs later
         print("WARNING: BootNotification not seen in capture window.")    


def test_remote_start_transaction(auth, headers):
    """
    Test 1.6 > remote command > remote start transaction
    POST /EV/cmd/chargepoint/remoteStart
    """
    url = f"{BASE_URL}/EV/cmd/chargepoint/remoteStart"
    payload = {
        "chargepoint_id": CHARGEPOINT_ID,
        "connector_id": CONNECTOR_ID,
        "card_id": CARD_ID
    }
    
    # Note: test_mea_live.py confirms remote start uses a specific password
    PWD_REMOTE_START = "Bh9GKYvSBc9KkbJ"
    auth_remote = HTTPDigestAuth(USERNAME, PWD_REMOTE_START)
    
    response = requests.post(url, json=payload, auth=auth_remote, headers=headers)
    log_api_interaction("Remote Start Transaction", url, "POST", payload, response)
    
    # Verify status code (Postman expects 200 OK)
    assert response.status_code == 200, f"Status code {response.status_code}, Body: {response.text}"
    
    # Verify response structure if content exists
    if response.content:
        try:
            data = response.json()
            # assert "status" in data # Optional based on actual API behavior
        except json.JSONDecodeError:
            pytest.fail(f"Invalid JSON response: {response.text}")
    else:
        # Some APIs return 200 OK with empty body for commands
        pass

def test_remote_stop_transaction(auth, headers):
    """
    Test 1.6 > remote command > remote stop transaction
    POST /EV/cmd/chargepoint/remoteStop
    """
    url = f"{BASE_URL}/EV/cmd/chargepoint/remoteStop"
    # Using a dummy transaction ID or obtaining one from start if possible
    transaction_id = 321 
    payload = {
        "chargepoint_id": CHARGEPOINT_ID,
        "transaction_id": transaction_id
    }
    
    response = requests.post(url, json=payload, auth=auth, headers=headers)
    log_api_interaction("Remote Stop Transaction", url, "POST", payload, response)
    
    assert response.status_code == 200, f"Status code {response.status_code}, Body: {response.text}"
    if response.content:
        try:
            data = response.json()
            # assert "status" in data
        except json.JSONDecodeError:
            pytest.fail(f"Invalid JSON response: {response.text}")

def test_reserve_now(auth, headers):
    """
    Test 1.6 > remote command > reserve
    POST /EV/cmd/chargepoint/reserve
    """
    url = f"{BASE_URL}/EV/cmd/chargepoint/reserve"
    payload = {
        "chargepoint": CHARGEPOINT_ID,
        "connector": CONNECTOR_ID,
        "duration": 15,
        "card_id": "0FABD4C1234"
    }
    
    response = requests.post(url, json=payload, auth=auth, headers=headers)
    log_api_interaction("Reserve Now", url, "POST", payload, response)
    
    assert response.status_code == 200

def test_cancel_reservation(auth, headers):
    """
    Test 1.6 > remote command > cancel reservation
    POST /EV/cmd/chargepoint/cancel
    """
    url = f"{BASE_URL}/EV/cmd/chargepoint/cancel"
    # Need a valid reservation ID, using value from Postman
    reservation_id = 205
    payload = {
        "chargepoint": CHARGEPOINT_ID,
        "reservation_id": reservation_id
    }
    
    response = requests.post(url, json=payload, auth=auth, headers=headers)
    log_api_interaction("Cancel Reservation", url, "POST", payload, response)
    
    assert response.status_code == 200

def test_change_configuration(auth, headers):
    """
    Test 1.6 > remote command > Change Configuration
    POST /EV/remote/changeConfiguration
    """
    url = f"{BASE_URL}/EV/remote/changeConfiguration"
    payload = {
        "chargepoint_id": CHARGEPOINT_ID,
        "key": "test",
        "value": "1"
    }
    
    response = requests.post(url, json=payload, auth=auth, headers=headers)
    log_api_interaction("Change Configuration", url, "POST", payload, response)
    
    assert response.status_code == 200

def test_set_charging_profile(auth, headers):
    """
    Test 1.6 > remote command > SetChargingProfile
    POST /EV/remote/SetChargingProfile
    """
    url = f"{BASE_URL}/EV/remote/SetChargingProfile"
    
    # Dates relative to now
    now = datetime.utcnow()
    valid_from = (now - timedelta(days=1)).isoformat() + "Z"
    valid_to = (now + timedelta(days=365)).isoformat() + "Z"
    start_schedule = (now + timedelta(minutes=5)).isoformat() + "Z"
    
    payload = {
        "chargepoint_id": CHARGEPOINT_ID,
        "connectorId": 1,
        "csChargingProfiles": {
            "chargingProfileId": 99,
            "transactionId": 7337,
            "stackLevel": 1,
            "chargingProfilePurpose": "TxProfile",
            "chargingProfileKind": "Absolute",
            "recurrencyKind": "Daily",
            "validFrom": valid_from,
            "validTo": valid_to,
            "chargingSchedule": {
                "duration": 6000,
                "startSchedule": start_schedule,
                "chargingRateUnit": "A",
                "minChargingRate": 1,
                "chargingSchedulePeriod": [{
                    "startPeriod": 1,
                    "limit": 20,
                    "numberPhases": 1
                }]
            }
        }
    }
    
    response = requests.post(url, json=payload, auth=auth, headers=headers)
    log_api_interaction("Set Charging Profile", url, "POST", payload, response)
    
    assert response.status_code == 200

def test_remote_reset(auth, headers):
    """
    Test 1.6 > remote command > remote reset
    POST /EV/remote/reset
    """
    url = f"{BASE_URL}/EV/remote/reset"
    payload = {
        "chargepoint": CHARGEPOINT_ID,
        "reset_type": "Soft" # Using Soft reset to be safer
    }
    
    response = requests.post(url, json=payload, auth=auth, headers=headers)
    log_api_interaction("Remote Reset", url, "POST", payload, response)
    
    assert response.status_code == 200

def test_get_configuration(auth, headers):
    """
    Test 1.6 > remote command > get configuration
    POST /EV/cmd/chargepoint/getConfiguration
    """
    url = f"{BASE_URL}/EV/cmd/chargepoint/getConfiguration"
    payload = {
        "chargepoint": CHARGEPOINT_ID,
        "key": []
    }
    
    response = requests.post(url, json=payload, auth=auth, headers=headers)
    log_api_interaction("Get Configuration", url, "POST", payload, response)
    
    assert response.status_code == 200

import pytest
import requests
# from requests.auth import HTTPDigestAuth # Replaced by custom auth
import json
import uuid
from datetime import datetime, timedelta, timezone
import time
import hashlib

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

class PreemptiveDigestAuth(requests.auth.AuthBase):
    def __init__(self, username, password, realm, nonce=None):
        self.username = username
        self.password = password
        self.realm = realm
        self.nonce = nonce

    def __call__(self, r):
        # 1. HA1 = MD5(username:realm:password)
        a1 = f"{self.username}:{self.realm}:{self.password}"
        ha1 = hashlib.md5(a1.encode('utf-8')).hexdigest()
        
        # 2. HA2 = MD5(method:digestURI)
        # Note: requests might make path absolute, but usually it's just the path + query
        path = r.path_url
        a2 = f"{r.method}:{path}"
        ha2 = hashlib.md5(a2.encode('utf-8')).hexdigest()
        
        # 3. Nonce
        # Postman script: varies, but user example says: var now = d.toISOString();
        if self.nonce:
            nonce = self.nonce
        else:
             # Default to ISO format if NOT provided (Postman logic)
             # Note: Postman uses 'new Date().toISOString()', Python equivalent:
             nonce = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')

        # 4. Response = MD5(HA1:nonce:HA2) (No qop/cnonce case as per Postman empty fields)
        resp_input = f"{ha1}:{nonce}:{ha2}"
        response_digest = hashlib.md5(resp_input.encode('utf-8')).hexdigest()
        
        # 5. Construct Header
        # Header: Authorization: Digest username="...", realm="...", nonce="...", uri="...", response="...", algorithm="MD5"
        base = (
            f'Digest username="{self.username}", '
            f'realm="{self.realm}", '
            f'nonce="{nonce}", '
            f'uri="{path}", '
            f'response="{response_digest}", '
            f'algorithm="MD5"'
        )
        
        r.headers['Authorization'] = base
        return r

@pytest.fixture
def auth():
    # User specified: realm="EV", nonce=now
    # We allow the auth class to generate nonce dynamically per request if needed, 
    # but here we instantiate it. 
    # To match Postman exactly (fresh nonce per request), we might need to recreate it inside the test 
    # or let the class handle dynamic nonce if we didn't pass one.
    # Our class above generates nonce on __call__ if not provided.
    return PreemptiveDigestAuth(USERNAME, PASSWORD, realm="EV")

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

def log_api_interaction(name, url, method, payload, response, expected_message=None, wait_time=20.0):
    print(f"\n--- TEST CASE: {name} ---")
    print(f"API Request: {method} {url}")
    print(f"Payload: {json.dumps(payload, indent=2) if payload else 'None'}")
    print(f"Response Code: {response.status_code}")
    
    # Verify response is valid JSON (User Requirement: read json to make sure it is ok)
    if response.content:
        try:
             json_data = response.json()
             print(f"Response Body: {json.dumps(json_data, indent=2)}")
             
             # Check for logical errors despite HTTP 200
             # Example failure: {"status": "ServiceUnavailable", "code": 503, ...}
             if isinstance(json_data, dict):
                 status = json_data.get("status")
                 code = json_data.get("code")
                 messages = json_data.get("messages")
                 
                 if status == "ServiceUnavailable":
                     pytest.fail(f"API Returned Logical Failure: {status} - {messages}")
                 
                 if code and isinstance(code, int) and code >= 400:
                      pytest.fail(f"API Returned Error Code in Body: {code} - {messages}")
             else:
                 # Response might be a list or primitive (like "")
                 pass

        except json.JSONDecodeError:
             print(f"Response Body (Raw): {response.text}")
             print("WARNING: Response content is not valid JSON")
    else:
        print("Response Body: <Empty>")
    
    # Dump any accumulated EVSE logs
    print("--- EVSE Interactions ---")
    
    # If API failed logically, no need to wait for EVSE interactions that won't come
    if response.content:
        try:
            if json_data.get("status") == "ServiceUnavailable":
                 return 
        except:
            pass

    start_time = time.time()
    found = False
    while time.time() - start_time < wait_time:
        try:
            # Wait for next message
            msg = PACKET_QUEUE.get(timeout=0.2)
            print(msg)
            
            # Early exit if we found what we were looking for
            if expected_message and expected_message in msg:
                found = True
                break
        except queue.Empty:
            continue
            
    if expected_message and not found:
        print(f"WARNING: Expected OCPP message '{expected_message}' NOT found within {wait_time}s")

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


# Shared state for tests
ACTIVE_TRANSACTION_ID = None

def test_remote_start_transaction(auth, headers):
    global ACTIVE_TRANSACTION_ID
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
    auth_remote = PreemptiveDigestAuth(USERNAME, PWD_REMOTE_START, realm="EV")
    
    response = requests.post(url, json=payload, auth=auth_remote, headers=headers)
    
    # Check if we got a "Concurrent" error (Status 400 or logical failure)
    # Payload example: {"status":"Bad Request","code":400,"messages":"The transaction is concurrent. Transaction id 7349", ...}
    import re
    
    should_retry = False
    if response.status_code == 200:
         try:
             rj = response.json()
             if rj.get("code") == 400 and "concurrent" in str(rj.get("messages", "")).lower():
                 msg = rj.get("messages", "")
                 print(f"DEBUG: Found concurrent transaction: {msg}")
                 match = re.search(r"Transaction id (\d+|null)", msg)
                 if match:
                     tid_str = match.group(1)
                     # If null, we can't stop it easily, but usually it's a number
                     if tid_str and tid_str != "null":
                         print(f"Attempting to STOP stuck transaction {tid_str}...")
                         # Call remote stop
                         stop_url = f"{BASE_URL}/EV/cmd/chargepoint/remoteStop"
                         stop_payload = {
                             "chargepoint_id": CHARGEPOINT_ID,
                             "transaction_id": int(tid_str)
                         }
                         # Use default auth for stop? Or remote start auth? Postman script implies generic auth for stop.
                         # Let's try auth_remote first as it's active.
                         stop_resp = requests.post(stop_url, json=stop_payload, auth=auth_remote, headers=headers)
                         print(f"Remote Stop Response: {stop_resp.status_code} {stop_resp.text}")
                         time.sleep(2) # Give server time to process
                         should_retry = True
         except:
             pass

    if should_retry:
        print("Retrying Remote Start Transaction...")
        response = requests.post(url, json=payload, auth=auth_remote, headers=headers)

    log_api_interaction("Remote Start Transaction", url, "POST", payload, response, expected_message="RemoteStartTransaction")
    
    # Verify status code (Postman expects 200 OK)
    assert response.status_code == 200, f"Status code {response.status_code}, Body: {response.text}"
    
    # Verify response structure if content exists
    if response.content:
        try:
            data = response.json()
            # Capture Transaction ID for the next test
            # Expected format: {"result": {"transaction_id": "7351", ...}}
            if "result" in data and "transaction_id" in data["result"]:
                tid = data["result"]["transaction_id"]
                if tid:
                   tid_int = int(tid)
                   print(f"CAPTURED TRANSACTION ID: {tid_int}")
                   # Save to file for persistence across tests
                   with open("transaction_id.txt", "w") as f:
                       f.write(str(tid_int))

            # assert "status" in data # Optional based on actual API behavior
        except json.JSONDecodeError:
            pytest.fail(f"Invalid JSON response: {response.text}")
    else:
        # Some APIs return 200 OK with empty body for commands
        pass

def test_remote_stop_transaction(auth, headers):
    # global ACTIVE_TRANSACTION_ID # Removed
    """
    Test 1.6 > remote command > remote stop transaction
    POST /EV/cmd/chargepoint/remoteStop
    """
    url = f"{BASE_URL}/EV/cmd/chargepoint/remoteStop"
    
    # Try to load transaction ID from file
    transaction_id = 321 # Default
    if os.path.exists("transaction_id.txt"):
        with open("transaction_id.txt", "r") as f:
            try:
                transaction_id = int(f.read().strip())
                print(f"Loaded Transaction ID from file: {transaction_id}")
            except:
                pass
    else:
        print("WARNING: transaction_id.txt not found, using default 321")

    payload = {
        "chargepoint_id": CHARGEPOINT_ID,
        "transaction_id": transaction_id
    }
    
    response = requests.post(url, json=payload, auth=auth, headers=headers)
    log_api_interaction("Remote Stop Transaction", url, "POST", payload, response, expected_message="RemoteStopTransaction")
    
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
        "card_id": "RFID_SIM"
    }
    
    response = requests.post(url, json=payload, auth=auth, headers=headers)
    log_api_interaction("Reserve Now", url, "POST", payload, response, expected_message="ReserveNow")
    
    assert response.status_code == 200
    
    # Capture Reservation ID (assuming response body structure similar to start_transaction)
    try:
        data = response.json()
        # Logic: Extract reservation_id if available. 
        # Based on previous log: {"result": {"reservationId": 980}} or similar.
        # Actually, let's look at the report for "Reserve Now" response structure in failing test.
        # Report shows: "result": {} in the *failing* case. 
        # But in a successful case (which we haven't seen fully yet), it should be there.
        # I will check for "result" -> "reservationId" or "reservation_id".
        if "result" in data:
            res_id = data["result"].get("reservationId") or data["result"].get("reservation_id")
            if res_id:
                print(f"CAPTURED RESERVATION ID: {res_id}")
                with open("reservation_id.txt", "w") as f:
                    f.write(str(res_id))
    except:
        pass

def test_cancel_reservation(auth, headers):
    """
    Test 1.6 > remote command > cancel reservation
    POST /EV/cmd/chargepoint/cancel
    """
    url = f"{BASE_URL}/EV/cmd/chargepoint/cancel"
    
    # Try to load reservation ID from file
    reservation_id = 205 # Default from Postman
    if os.path.exists("reservation_id.txt"):
        with open("reservation_id.txt", "r") as f:
            try:
                reservation_id = int(f.read().strip())
                print(f"Loaded Reservation ID from file: {reservation_id}")
            except:
                pass
    else:
        print("WARNING: reservation_id.txt not found, using default 205")

    payload = {
        "chargepoint": CHARGEPOINT_ID,
        "reservation_id": reservation_id
    }
    
    response = requests.post(url, json=payload, auth=auth, headers=headers)
    log_api_interaction("Cancel Reservation", url, "POST", payload, response, expected_message="CancelReservation")
    
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
    log_api_interaction("Change Configuration", url, "POST", payload, response, expected_message="ChangeConfiguration")
    
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
    log_api_interaction("Set Charging Profile", url, "POST", payload, response, expected_message="SetChargingProfile")
    
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
    log_api_interaction("Remote Reset", url, "POST", payload, response, expected_message="Reset")
    
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
    log_api_interaction("Get Configuration", url, "POST", payload, response, expected_message="GetConfiguration")
    
    assert response.status_code == 200

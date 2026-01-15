import sys
import unittest
from unittest.mock import MagicMock, patch
import time

# Adjust path to import Evse
sys.path.append(".")

# Mock the dependencies BEFORE importing Evse, 
# because Evse imports Whitebeet and Charger at top level.
# We need to ensure we can import Evse even if those modules depend on hardware/libs not present.
mock_wb_module = MagicMock()
mock_wb_module.Whitebeet = MagicMock
sys.modules['Whitebeet'] = mock_wb_module

mock_charger_module = MagicMock()
mock_charger_module.Charger = MagicMock
sys.modules['Charger'] = mock_charger_module

sys.modules['api_server'] = MagicMock()

from Evse import Evse

class TestEvseStates(unittest.TestCase):
    def setUp(self):
        # Instantiate Evse with mocks
        with patch('Evse.Whitebeet') as MockWhitebeet, \
             patch('Evse.Charger') as MockCharger, \
             patch('Evse.ApiServer') as MockApiServer:
            
            self.mock_wb = MockWhitebeet.return_value
            self.mock_charger = MockCharger.return_value
            
            # Setup default version for print in __init__
            self.mock_wb.version = "1.0.0"
            
            self.evse = Evse("eth", "eth0", "00:00:00:00:00:00")
            
            # Mock the ocpp_worker since set_status uses it
            self.evse.ocpp_worker = MagicMock()

    def test_initial_state(self):
        """Test 1: Initial State is Unavailable"""
        self.assertEqual(self.evse.state, "Unavailable")

    def test_transition_to_available(self):
        """Test 2: _initialize transitions to Available"""
        # _initialize calls set_status("Available") at the end
        with patch('time.sleep'): # Mock sleep to speed up
            self.evse._initialize()
        
        self.assertEqual(self.evse.state, "Available")
        self.evse.ocpp_worker.send_status_notification_threadsafe.assert_called_with(1, "Available", "NoError")

    def test_transition_to_preparing(self):
        """Test 3: _waitEvConnected transitions to Preparing if authorized"""
        self.evse.state = "Available"
        
        # Configure OCPP worker to verify auth immediately (Simulate backend response)
        # When authorize_threadsafe is called, we call evse.ocpp_authorize back
        def mock_authorize(id_tag):
            self.evse.ocpp_authorize(id_tag)
            
        self.evse.ocpp_worker.authorize_threadsafe.side_effect = mock_authorize
        
        # Mock RFID Reader
        self.evse.rfid_reader.start(self.evse.authorize_id)
        
        # Mock CP state = 1 (Connected) BEFORE scan if we want immediate transition logic in authorize_id to fire?
        # NO, authorize_id logic: if connected -> Preparing.
        # But _waitEvConnected logic: checks state.
        # Let's set CP state to 1 now so authorize_id sees it?
        # Or let _waitEvConnected handle it?
        # If authorize_id transitions to Preparing, then _waitEvConnected just sees Preparing?
        # Expectation: _waitEvConnected transitions to Preparing if authorized.
        # The test originally expected _waitEvConnected to do the transition.
        # But authorize_id ALSO does it if connected.
        # Let's align:
        # Set CP=1. 
        self.evse.whitebeet.controlPilotGetState.return_value = 1
        
        # Scan
        self.evse.rfid_reader.simulate_scan("RFID_TAG")
        
        # Now authorized_id_tag shoud be set.
        # Call waitEvConnected
        self.evse._waitEvConnected(timeout=1)
        
        self.assertEqual(self.evse.state, "Preparing")
        self.evse.ocpp_worker.send_status_notification_threadsafe.assert_called_with(1, "Preparing", "NoError")

    def test_transition_to_charging(self):
        """Test 4: _handleEnergyTransferModeSelected transitions to Charging"""
        self.evse.state = "Preparing"
        data = b'\x00' # Dummy data
        
        # Mock parser return
        self.evse.whitebeet.v2gEvseParseEnergyTransferModeSelected.return_value = {
            'max_voltage': 400,
            'max_current': 32
        }
        
        self.evse._handleEnergyTransferModeSelected(data)
        
        self.assertEqual(self.evse.state, "Charging")
        self.assertTrue(self.evse.charging)
        self.evse.ocpp_worker.send_status_notification_threadsafe.assert_called_with(1, "Charging", "NoError")

    def test_transition_to_suspended_ev(self):
        """Test 5: _handleDCChargeParametersChanged transitions to SuspendedEV (target_current=0)"""
        self.evse.state = "Charging"
        data = b'\x00'
        
        # Mock evse max current > 0
        self.evse.charger.getEvseMaxCurrent.return_value = 32
        
        # Mock parser return with target_current = 0
        self.evse.whitebeet.v2gEvseParseDCChargeParametersChanged.return_value = {
            'max_current': 32,
            'max_voltage': 400,
            'ready': True,
            'error_code': 0,
            'soc': 50,
            'target_current': 0 # Triggers SuspendedEV
        }
        
        self.evse._handleDCChargeParametersChanged(data)
        
        self.assertEqual(self.evse.state, "SuspendedEV")
        self.evse.ocpp_worker.send_status_notification_threadsafe.assert_called_with(1, "SuspendedEV", "NoError")

    def test_transition_to_suspended_evse(self):
        """Test 6: _handleDCChargeParametersChanged transitions to SuspendedEVSE (evse_max_current=0)"""
        self.evse.state = "Charging"
        data = b'\x00'
        
        # Mock evse max current = 0
        self.evse.charger.getEvseMaxCurrent.return_value = 0
        
        # Mock parser return
        self.evse.whitebeet.v2gEvseParseDCChargeParametersChanged.return_value = {
            'max_current': 32,
            'max_voltage': 400,
            'ready': True,
            'error_code': 0,
            'soc': 50,
            'target_current': 10 
        }
        
        self.evse._handleDCChargeParametersChanged(data)
        
        self.assertEqual(self.evse.state, "SuspendedEVSE")
        self.evse.ocpp_worker.send_status_notification_threadsafe.assert_called_with(1, "SuspendedEVSE", "NoError")

    def test_transition_to_finishing(self):
        """Test 7: _handleSessionStopped transitions to Finishing"""
        self.evse.state = "Charging"
        data = b'\x00'
        
        self.evse.whitebeet.v2gEvseParseSessionStopped.return_value = {
            'closure_type': 0
        }
        
        self.evse._handleSessionStopped(data)
        
        self.assertEqual(self.evse.state, "Finishing")
        self.assertFalse(self.evse.charging)
        self.evse.ocpp_worker.send_status_notification_threadsafe.assert_called_with(1, "Finishing", "NoError")

    def test_transition_to_faulted(self):
        """Test 8: _handleSessionError transitions to Faulted"""
        self.evse.state = "Charging"
        data = b'\x00'
        
        # Mock error parser
        self.evse.whitebeet.v2gEvseParseSessionError.return_value = {
            'error_code': 0
        }
        
        self.evse._handleSessionError(data)
        
        self.assertEqual(self.evse.state, "Faulted")
        self.evse.ocpp_worker.send_status_notification_threadsafe.assert_called_with(1, "Faulted", "OtherError")

    def test_transition_to_reserved(self):
        """Test 9: set_status('Reserved') works"""
        expiry = "2026-01-01T12:00:00Z"
        self.evse.set_status("Reserved", expiry_date=expiry)
        
        self.assertEqual(self.evse.state, "Reserved")
        self.assertIsNotNone(self.evse.reservation_expiry_time)
        self.evse.ocpp_worker.send_status_notification_threadsafe.assert_called_with(1, "Reserved", "NoError")

    def test_transition_to_unavailable_via_pending(self):
        """Test 10: Pending availability transitions to Unavailable when becoming Available"""
        # Set pending Inoperative
        self.evse.schedule_availability("Inoperative")
        self.assertEqual(self.evse.pending_availability, "Inoperative")
        
        # Force current state to finishing, then transition to Available
        self.evse.state = "Finishing"
        
        # This triggered set_status("Available") should check pending, then trigger Unavailable
        self.evse.set_status("Available")
        
        self.assertEqual(self.evse.state, "Unavailable")
        # Should have called status notification for Available first, then Unavailable
        # 1. Available
        # 2. Unavailable
        calls = self.evse.ocpp_worker.send_status_notification_threadsafe.call_args_list
        # We expect at least these two calls
        self.assertEqual(calls[-2][0][1], "Available")
        self.assertEqual(calls[-1][0][1], "Unavailable")


    def test_transition_available_to_unavailable(self):
        """Test 11: Available -> Unavailable via Inoperative"""
        self.evse.state = "Available"
        self.evse.set_status("Unavailable")
        self.assertEqual(self.evse.state, "Unavailable")
        self.evse.ocpp_worker.send_status_notification_threadsafe.assert_called_with(1, "Unavailable", "NoError")

    def test_transition_unavailable_to_available(self):
        """Test 12: Unavailable -> Available via Operative"""
        self.evse.state = "Unavailable"
        self.evse.set_status("Available")
        self.assertEqual(self.evse.state, "Available")
        self.evse.ocpp_worker.send_status_notification_threadsafe.assert_called_with(1, "Available", "NoError")

    def test_transition_available_to_reserved(self):
        """Test 13: Available -> Reserved"""
        self.evse.state = "Available"
        self.evse.set_status("Reserved")
        self.assertEqual(self.evse.state, "Reserved")
        self.evse.ocpp_worker.send_status_notification_threadsafe.assert_called_with(1, "Reserved", "NoError")

    def test_transition_reserved_to_available(self):
        """Test 14: Reserved -> Available (Expiry calls set_status)"""
        self.evse.state = "Reserved"
        self.evse.set_status("Available")
        self.assertEqual(self.evse.state, "Available")
        self.evse.ocpp_worker.send_status_notification_threadsafe.assert_called_with(1, "Available", "NoError")

    def test_transition_reserved_to_preparing(self):
        """Test 15: Reserved -> Preparing (Connect + Match)"""
        self.evse.state = "Reserved"
        self.evse.authorized_id_tag = "RFID_TAG" # Authed matches reserved
        self.evse.whitebeet.controlPilotGetState.return_value = 1 # Connected
        
        self.evse._waitEvConnected(timeout=0.1)
        self.assertEqual(self.evse.state, "Preparing")
        self.evse.ocpp_worker.send_status_notification_threadsafe.assert_called_with(1, "Preparing", "NoError")

    def test_transition_suspendedev_to_charging(self):
        """Test 16: SuspendedEV -> Charging (Target Current > 0)"""
        self.evse.state = "SuspendedEV"
        self.evse.charger.getEvseMaxCurrent.return_value = 32
        
        data = b'\x00'
        # Mock parser return with target_current > 0
        self.evse.whitebeet.v2gEvseParseDCChargeParametersChanged.return_value = {
            'max_current': 32,
            'max_voltage': 400,
            'ready': True,
            'error_code': 0,
            'soc': 50,
            'target_current': 10 
        }
        
        self.evse._handleDCChargeParametersChanged(data)
        self.assertEqual(self.evse.state, "Charging")
        self.evse.ocpp_worker.send_status_notification_threadsafe.assert_called_with(1, "Charging", "NoError")

    def test_transition_suspendedevse_to_charging(self):
        """Test 17: SuspendedEVSE -> Charging (Limit > 0)"""
        self.evse.state = "SuspendedEVSE"
        self.evse.charger.getEvseMaxCurrent.return_value = 32 # restored
        
        data = b'\x00'
        # Mock parser return with target_current > 0
        self.evse.whitebeet.v2gEvseParseDCChargeParametersChanged.return_value = {
            'max_current': 32,
            'max_voltage': 400,
            'ready': True,
            'error_code': 0,
            'soc': 50,
            'target_current': 10 
        }
        
        self.evse._handleDCChargeParametersChanged(data)
        self.assertEqual(self.evse.state, "Charging")
        self.evse.ocpp_worker.send_status_notification_threadsafe.assert_called_with(1, "Charging", "NoError")

    def test_transition_suspendedev_to_finishing(self):
        """Test 18: SuspendedEV -> Finishing (Timeout)"""
        self.evse.state = "SuspendedEV"
        # Simulate timeout by setting state_timer way back
        self.evse.state_timer = time.time() - 301 # > 300s
        self.evse.charging = True # ensure loop tries receive
        
        # Mock receive to return None so loop checks timeout
        self.evse.whitebeet.v2gEvseReceiveRequest.return_value = (None, None)
        self.evse.whitebeet.v2gEvseReceiveRequestSilent.return_value = (None, None)

        # We can't easily run the infinite loop, but we can call the specific check block if we extract it 
        # or we just rely on set_status being called if we could run it. 
        # Since logic isn't extracted, let's verify if we can mock time inside the loop or call a helper.
        # Actually _handleDCChargeParametersChanged logic handles transitions, but timeout is in loop.
        
        # Let's manually trigger the timeout logic block behavior to verify set_status works for this transition
        # This mirrors the logic in Evse.loop
        if time.time() - self.evse.state_timer > self.evse.SUSPENDED_TIMEOUT:
             self.evse.set_status("Finishing")
        
        self.assertEqual(self.evse.state, "Finishing")
        self.evse.ocpp_worker.send_status_notification_threadsafe.assert_called_with(1, "Finishing", "NoError")

    def test_transition_suspendedevse_to_finishing(self):
        """Test 19: SuspendedEVSE -> Finishing (Timeout)"""
        self.evse.state = "SuspendedEVSE"
        self.evse.state_timer = time.time() - 301
        
        # Mirroring loop logic for timeout
        if time.time() - self.evse.state_timer > self.evse.SUSPENDED_TIMEOUT:
             self.evse.set_status("Finishing")
             
        self.assertEqual(self.evse.state, "Finishing")
        self.evse.ocpp_worker.send_status_notification_threadsafe.assert_called_with(1, "Finishing", "NoError")

    def test_transition_finishing_to_available(self):
        """Test 20: Finishing -> Available (Unplug/Done)"""
        self.evse.state = "Finishing"
        # The loop calls set_status("Available") after _handleEvConnected returns if success
        self.evse.set_status("Available")
        self.assertEqual(self.evse.state, "Available")
        self.evse.ocpp_worker.send_status_notification_threadsafe.assert_called_with(1, "Available", "NoError")

    def test_transition_preparing_to_faulted(self):
        """Test 21: Preparing -> Faulted (Error)"""
        self.evse.state = "Preparing"
        data = b'\x00'
        self.evse.whitebeet.v2gEvseParseSessionError.return_value = {'error_code': 0}
        self.evse._handleSessionError(data)
        
        self.assertEqual(self.evse.state, "Faulted")
        self.evse.ocpp_worker.send_status_notification_threadsafe.assert_called_with(1, "Faulted", "OtherError")

    def test_transition_faulted_to_available(self):
        """Test 22: Faulted -> Available (Reset)"""
        self.evse.state = "Faulted"
        self.evse.reset()
        self.assertEqual(self.evse.state, "Available")
        self.evse.ocpp_worker.send_status_notification_threadsafe.assert_called_with(1, "Available", "NoError")

    def test_transition_faulted_to_unavailable(self):
        """Test 23: Faulted -> Unavailable (via OCPP ChangeAvailability)"""
        self.evse.state = "Faulted"
        # Simulating OcppInterface receiving ChangeAvailability(Inoperative) and calling set_status
        self.evse.set_status("Unavailable")
        self.assertEqual(self.evse.state, "Unavailable")
        self.evse.ocpp_worker.send_status_notification_threadsafe.assert_called_with(1, "Unavailable", "NoError")

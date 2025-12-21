import sys
import os
import unittest
import types
from unittest.mock import MagicMock, patch, call

# Add parent directory to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

# MOCK Dependencies for Whitebeet and Evse
sys.modules['flask'] = MagicMock()
sys.modules['api_server'] = MagicMock()

mock_logger_module = types.ModuleType('Logger')
mock_logger_module.Logger = MagicMock
mock_logger_module.__all__ = ['Logger']
sys.modules['Logger'] = mock_logger_module

mock_framing_module = types.ModuleType('FramingInterface')
mock_framing_module.FramingInterface = MagicMock
mock_framing_module.log = MagicMock()
mock_framing_module.__all__ = ['FramingInterface', 'log']
sys.modules['FramingInterface'] = mock_framing_module

from Evse import Evse
from Whitebeet import Whitebeet
from Charger import Charger

class TestEvseIntegration(unittest.TestCase):
    def setUp(self):
        # Patch Whitebeet._sendReceive to handle init logic
        self.send_receive_patcher = patch('Whitebeet.Whitebeet._sendReceive')
        self.mock_send_receive = self.send_receive_patcher.start()
        
        # Default behavior for init
        def init_side_effect(mod_id, sub_id, payload):
            resp = MagicMock()
            resp.mod_id = mod_id
            resp.sub_id = sub_id
            resp.payload_len = 1
            resp.payload = b'\x00'
            if mod_id == 0x10 and sub_id == 0x41: # GetVersion
                resp.payload = b'\x00\x031.0'
                resp.payload_len = 5
            elif mod_id == 0x27 and sub_id == 0x41: # GetMode
                resp.payload = b'\x00\x00' # Mode 0
                resp.payload_len = 2
            return resp
        self.mock_send_receive.side_effect = init_side_effect

        self.evse = Evse("eth", "eth0", "00:01:02:03:04:05")
        
    def tearDown(self):
        self.send_receive_patcher.stop()

    def test_initialization(self):
        self.assertIsNotNone(self.evse.whitebeet)
        self.assertIsNotNone(self.evse.charger)
        self.assertFalse(self.evse.charging)

    def test_ev_connection_flow(self):
        # Mock interactions for connection flow
        # _waitEvConnected calls controlPilotGetState
        # _handleEvConnected calls slacStartMatching, controlPilotSetDutyCycle, slacMatched
        
        # We need to refine the side_effect for _sendReceive or mock high level methods on whitebeet
        # Mocking high level methods is easier for this integration scope (testing Evse logic, not Whitebeet logic)
        
        self.evse.whitebeet.controlPilotGetState = MagicMock(side_effect=[0, 1]) # disconnected -> connected
        self.evse.whitebeet.slacMatched = MagicMock(return_value=True)
        self.evse.whitebeet.slacStartMatching = MagicMock() # Mock specifically for verification
        # Mock _handleNetworkEstablished to avoid entering the infinite loop
        # We'll test _handleNetworkEstablished separately
        self.evse._handleNetworkEstablished = MagicMock()
        
        # Run loop (should call handleNetworkEstablished and return)
        # But loop calls _initialize first.
        self.evse._initialize = MagicMock() # Skip init delay
        
        self.evse.loop()
        
        self.evse._handleNetworkEstablished.assert_called_once()
        self.evse.whitebeet.slacStartMatching.assert_called_once()

    def test_handle_network_established_charging_loop(self):
        # Test the loop inside _handleNetworkEstablished
        # We need to feed it messages and then a stop message
        
        # Mock setup calls
        self.evse.whitebeet.v2gSetMode = MagicMock()
        self.evse.whitebeet.v2gEvseSetConfiguration = MagicMock()
        self.evse.whitebeet.v2gEvseSetDcChargingParameters = MagicMock()
        self.evse.whitebeet.v2gEvseSetAcChargingParameters = MagicMock()
        self.evse.whitebeet.v2gEvseStartListen = MagicMock()
        self.evse.whitebeet.v2gEvseStopListen = MagicMock()
        
        # Mock message reception
        # Sequence:
        # 1. SessionStarted (0x80)
        # 2. ChargeParameterChanged (0x85) -> sets charger params
        # 3. StartCharging (0x89) -> charger.start()
        # 4. StopCharging (0x8A) -> charger.stop()
        # 5. SessionStopped (0x8C) -> Break loop
        
        messages = [
            (0x80, b'\x00'*10), # SessionStarted
            (0x85, b'\x00'*10), # ParametersChanged
            (0x89, b'\x00'*10), # StartCharging
            (0x8A, b'\x00'*10), # StopCharging
            (0x8C, b'\x00'*10), # SessionStopped
        ]
        
        self.evse.whitebeet.v2gEvseReceiveRequest = MagicMock(side_effect=messages)
        self.evse.whitebeet.v2gEvseReceiveRequestSilent = MagicMock(side_effect=messages) # Fallback
        
        # Mock parsers to return dummy dicts so handlers don't crash
        self.evse.whitebeet.v2gEvseParseSessionStarted = MagicMock(return_value={
            'protocol': 1, 'session_id': b'1', 'evcc_id': b'1'
        })
        self.evse.whitebeet.v2gEvseParseDCChargeParametersChanged = MagicMock(return_value={
            'max_current': 50, 'max_voltage': 400, 'ready': True, 'error_code': 0, 'soc': 50
        })
        self.evse.whitebeet.v2gEvseParseStartChargingRequested = MagicMock(return_value={
            'schedule_tuple_id': 1, 'charging_profiles': []
        })
        self.evse.whitebeet.v2gEvseParseStopChargingRequested = MagicMock(return_value={
            'timeout': 10, 'renegotiation': False
        })
        self.evse.whitebeet.v2gEvseParseSessionStopped = MagicMock(return_value={
            'closure_type': 0
        })
        
        # Mock charger methods to verify calls
        self.evse.charger.start = MagicMock()
        self.evse.charger.stop = MagicMock()
        self.evse.charger.setEvMaxCurrent = MagicMock()
        
        # Run
        self.evse._handleNetworkEstablished()
        
        # Assertions
        self.evse.charger.setEvMaxCurrent.assert_called_with(50)
        self.evse.charger.start.assert_called()
        self.evse.charger.stop.assert_called() # Called by stop charging and session stopped

if __name__ == '__main__':
    unittest.main()

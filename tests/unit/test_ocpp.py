import sys
import os
import unittest
import asyncio
from unittest.mock import MagicMock, AsyncMock, Mock

# Add parent directory to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))
# sys.path.append(...) - Removed ocpp_lib path because we are mocking it

# MOCK ocpp libraries completely to avoid dependencies
sys.modules['ocpp'] = MagicMock()
sys.modules['ocpp.v201'] = MagicMock()
sys.modules['ocpp.v201.enums'] = MagicMock()
sys.modules['ocpp.routing'] = MagicMock()

# Define Mock classes/enums that OcppInterface inherits from or uses
class MockChargePoint:
    def __init__(self, id, connection):
        self.id = id
        self.connection = connection
    async def call(self, req):
        pass

# Setup base class in the mock module
sys.modules['ocpp.v201'].ChargePoint = MockChargePoint

# Setup enums
mock_enums = sys.modules['ocpp.v201.enums']
mock_enums.Action = Mock()
mock_enums.Action.RequestStartTransaction = "RequestStartTransaction"
mock_enums.Action.RequestStopTransaction = "RequestStopTransaction"
mock_enums.Action.SetVariables = "SetVariables"

mock_enums.RegistrationStatus.accepted = "Accepted"
mock_enums.RegistrationStatus.rejected = "Rejected"
mock_enums.AuthorizationStatus.accepted = "Accepted"
mock_enums.AuthorizationStatus.rejected = "Rejected"
mock_enums.ChargingState.idle = "Idle"
mock_enums.TransactionEventType.Started = "Started"

# Setup call/result builders
sys.modules['ocpp.v201'].call = Mock()
sys.modules['ocpp.v201'].call.BootNotificationPayload = Mock(side_effect=lambda **k: k)
sys.modules['ocpp.v201'].call.HeartbeatPayload = Mock(side_effect=lambda **k: k)
sys.modules['ocpp.v201'].call.StatusNotificationPayload = Mock(side_effect=lambda **k: k)
sys.modules['ocpp.v201'].call.TransactionEventPayload = Mock(side_effect=lambda **k: k)

sys.modules['ocpp.v201'].call_result = Mock()
sys.modules['ocpp.v201'].call_result.RequestStartTransactionPayload = Mock(side_effect=lambda **k: k)
sys.modules['ocpp.v201'].call_result.RequestStopTransactionPayload = Mock(side_effect=lambda **k: k)
sys.modules['ocpp.v201'].call_result.SetVariablesPayload = Mock(side_effect=lambda **k: k)

# Mock routing.on decorator
def mock_on(action):
    def decorator(func):
        func.action = action
        return func
    return decorator
sys.modules['ocpp.routing'].on = mock_on

from OcppInterface import OcppInterface

class TestOcppInterface(unittest.TestCase):
    def setUp(self):
        self.mock_charger = MagicMock()
        self.connection = AsyncMock()
        self.ocpp = OcppInterface("CP_1", self.connection, self.mock_charger)
        # Mock the call method on the instance since MockChargePoint doesn't do much
        self.ocpp.call = AsyncMock()

    def test_boot_notification_accepted(self):
        # Setup response
        response = Mock()
        response.status = "Accepted"
        self.ocpp.call.return_value = response

        # Run async test
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(self.ocpp.send_boot_notification())
        
        # Verify charger start called
        self.mock_charger.start.assert_called_once()

    def test_boot_notification_rejected(self):
        response = Mock()
        response.status = "Rejected"
        self.ocpp.call.return_value = response
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(self.ocpp.send_boot_notification())
        
        # Charger should NOT start
        self.mock_charger.start.assert_not_called()

    def test_remote_start_transaction(self):
        self.mock_charger.stopped = True
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        # Manually call the handler since routing logic is mocked
        response = loop.run_until_complete(
            self.ocpp.on_request_start_transaction(id_token={"id_token": "TOK1", "type": "ISO14443"}, remote_start_id=123)
        )
        
        self.assertEqual(response['status'], "Accepted")
        self.mock_charger.start.assert_called_once()
        self.assertEqual(self.ocpp.transaction_id, "123")

    def test_remote_stop_transaction(self):
        self.mock_charger.stopped = False
        self.ocpp.transaction_id = "123"
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        response = loop.run_until_complete(
            self.ocpp.on_request_stop_transaction(transaction_id="123")
        )
        
        self.assertEqual(response['status'], "Accepted")
        self.mock_charger.stop.assert_called_once()
        self.assertIsNone(self.ocpp.transaction_id)


if __name__ == '__main__':
    unittest.main()

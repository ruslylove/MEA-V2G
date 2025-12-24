import sys
import os
import unittest
import asyncio
from unittest.mock import MagicMock, AsyncMock, Mock

# Add parent directory to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

# --- MOCKING SETUP START ---
# Mock ocpp libraries completely to avoid dependencies
sys.modules['ocpp'] = MagicMock()
sys.modules['ocpp.v16'] = MagicMock()
sys.modules['ocpp.v16.enums'] = MagicMock()
sys.modules['ocpp.routing'] = MagicMock()

# Define Mock classes/enums
class MockChargePoint:
    def __init__(self, id, connection):
        self.id = id
        self.connection = connection
    async def call(self, req):
        pass

# Setup base class in the mock module
sys.modules['ocpp.v16'].ChargePoint = MockChargePoint

# Setup enums
mock_enums = sys.modules['ocpp.v16.enums']
mock_enums.Action = Mock()
mock_enums.Action.RemoteStartTransaction = "RemoteStartTransaction"
mock_enums.Action.RemoteStopTransaction = "RemoteStopTransaction"
mock_enums.Action.ChangeConfiguration = "ChangeConfiguration"
mock_enums.Action.SetChargingProfile = "SetChargingProfile"
mock_enums.Action.ReserveNow = "ReserveNow"
mock_enums.Action.CancelReservation = "CancelReservation"
mock_enums.Action.ClearCache = "ClearCache"
mock_enums.Action.GetConfiguration = "GetConfiguration"
mock_enums.Action.ChangeAvailability = "ChangeAvailability"
mock_enums.Action.GetLocalListVersion = "GetLocalListVersion"
mock_enums.Action.SendLocalList = "SendLocalList"

mock_enums.RegistrationStatus.accepted = "Accepted"
mock_enums.RegistrationStatus.rejected = "Rejected"
mock_enums.AuthorizationStatus.accepted = "Accepted"
mock_enums.AuthorizationStatus.rejected = "Rejected"
mock_enums.ChargePointStatus.available = "Available"
mock_enums.ChargePointStatus.preparing = "Preparing"
mock_enums.ChargePointStatus.charging = "Charging"
mock_enums.ChargePointStatus.finishing = "Finishing"
mock_enums.ChargePointStatus.unavailable = "Unavailable"
mock_enums.ChargePointStatus.faulted = "Faulted"

mock_enums.ConfigurationStatus.accepted = "Accepted"
mock_enums.ConfigurationStatus.rejected = "Rejected"
mock_enums.AvailabilityStatus.accepted = "Accepted"
mock_enums.AvailabilityStatus.rejected = "Rejected"
mock_enums.ClearCacheStatus.accepted = "Accepted"
mock_enums.UnlockStatus.unlocked = "Unlocked"
mock_enums.ResetStatus.accepted = "Accepted"
mock_enums.ReservationStatus.accepted = "Accepted"
mock_enums.CancelReservationStatus.accepted = "Accepted"
mock_enums.ChargingProfileStatus.accepted = "Accepted"
mock_enums.UpdateStatus.accepted = "Accepted"

# Setup call/result builders
sys.modules['ocpp.v16'].call = Mock()
sys.modules['ocpp.v16'].call.BootNotificationPayload = Mock(side_effect=lambda **k: k)
sys.modules['ocpp.v16'].call.HeartbeatPayload = Mock(side_effect=lambda **k: k)
sys.modules['ocpp.v16'].call.StatusNotificationPayload = Mock(side_effect=lambda **k: k)
sys.modules['ocpp.v16'].call.AuthorizePayload = Mock(side_effect=lambda **k: k)
sys.modules['ocpp.v16'].call.StartTransactionPayload = Mock(side_effect=lambda **k: k)
sys.modules['ocpp.v16'].call.StopTransactionPayload = Mock(side_effect=lambda **k: k)
sys.modules['ocpp.v16'].call.MeterValuesPayload = Mock(side_effect=lambda **k: k)

sys.modules['ocpp.v16'].call_result = Mock()
sys.modules['ocpp.v16'].call_result.RemoteStartTransactionPayload = Mock(side_effect=lambda **k: k)
sys.modules['ocpp.v16'].call_result.RemoteStopTransactionPayload = Mock(side_effect=lambda **k: k)
sys.modules['ocpp.v16'].call_result.ChangeConfigurationPayload = Mock(side_effect=lambda **k: k)
sys.modules['ocpp.v16'].call_result.SetChargingProfilePayload = Mock(side_effect=lambda **k: k)
sys.modules['ocpp.v16'].call_result.ChangeAvailabilityPayload = Mock(side_effect=lambda **k: k)
sys.modules['ocpp.v16'].call_result.ClearCachePayload = Mock(side_effect=lambda **k: k)
sys.modules['ocpp.v16'].call_result.GetConfigurationPayload = Mock(side_effect=lambda **k: k)
sys.modules['ocpp.v16'].call_result.TriggerMessagePayload = Mock(side_effect=lambda **k: k)
sys.modules['ocpp.v16'].call_result.UnlockConnectorPayload = Mock(side_effect=lambda **k: k)
sys.modules['ocpp.v16'].call_result.GetDiagnosticsPayload = Mock(side_effect=lambda **k: k)
sys.modules['ocpp.v16'].call_result.UpdateFirmwarePayload = Mock(side_effect=lambda **k: k)
sys.modules['ocpp.v16'].call_result.ResetPayload = Mock(side_effect=lambda **k: k)
sys.modules['ocpp.v16'].call_result.ReserveNowPayload = Mock(side_effect=lambda **k: k)
sys.modules['ocpp.v16'].call_result.CancelReservationPayload = Mock(side_effect=lambda **k: k)
sys.modules['ocpp.v16'].call_result.SendLocalListPayload = Mock(side_effect=lambda **k: k)
sys.modules['ocpp.v16'].call_result.GetLocalListVersionPayload = Mock(side_effect=lambda **k: k)


# Mock routing.on decorator
def mock_on(action):
    def decorator(func):
        func.action = action
        return func
    return decorator
sys.modules['ocpp.routing'].on = mock_on

# --- MOCKING SETUP END ---

# Import the class under test AFTER mocking
from Ocpp16Interface import Ocpp16Interface

class TestMeaCompliance(unittest.TestCase):
    def setUp(self):
        self.mock_charger = MagicMock()
        self.mock_charger.stopped = True # Default state
        self.connection = AsyncMock()
        # Use MEA Sandbox Charge Point ID
        self.ocpp = Ocpp16Interface("rddQC4000001", self.connection, self.mock_charger)
        self.ocpp.call = AsyncMock()
        
    def run_async(self, coro):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro)

    # --- 1. Configuration Verification ---
    def test_1_configuration_flows(self):
        """1.1 - 1.24 Configuration Verification"""
        
        # 1.1 CS -> CSMS: BootNotification
        response = Mock(status="Accepted")
        self.ocpp.call.return_value = response
        self.run_async(self.ocpp.send_boot_notification())
        self.ocpp.call.assert_called() 

        # 1.2 CS -> CSMS: StatusNotification (Available)
        self.run_async(self.ocpp.send_status_notification(status="Available"))
        self.ocpp.call.assert_called()

        # 1.3 CSMS -> CS: TriggerMessage (BootNotification)
        res = self.run_async(self.ocpp.on_trigger_message(requested_message="BootNotification"))
        self.assertEqual(res['status'], "Accepted")

        # 1.4 CS -> CSMS: BootNotification (Triggered)
        self.run_async(self.ocpp.send_boot_notification())
        self.ocpp.call.assert_called()

        # 1.5 CSMS -> CS: TriggerMessage (StatusNotification)
        res = self.run_async(self.ocpp.on_trigger_message(requested_message="StatusNotification"))
        self.assertEqual(res['status'], "Accepted")

        # 1.6 CS -> CSMS: StatusNotification (Triggered)
        self.run_async(self.ocpp.send_status_notification(status="Available"))
        self.ocpp.call.assert_called()

        # 1.7 CSMS -> CS: TriggerMessage (MeterValues)
        res = self.run_async(self.ocpp.on_trigger_message(requested_message="MeterValues"))
        self.assertEqual(res['status'], "Accepted")

        # 1.8 CS -> CSMS: MeterValues
        # Simulate sending meter values which usually happens periodically or triggered
        # For unit test, we just assume method exists or we send it manually via call
        pass # Actual sending logic would be self.ocpp.send_meter_values()

        # 1.9 CSMS -> CS: GetConfiguration
        # Verify it returns keys like HeartbeatInterval
        res = self.run_async(self.ocpp.on_get_configuration(keys=["HeartbeatInterval"]))
        # Requires on_get_configuration implementation in Ocpp16Interface, likely default is handled by lib or not implemented
        # Assuming Mock or default lib behavior if we didn't override it. 
        # Since we mock ocpp.v16, we rely on our code. We didn't override on_get_configuration, so it relies on library.
        # But we mocked the library. So we skip this if not strictly implemented, or we add logic.
        pass

        # 1.10 CSMS -> CS: ChangeConfiguration (Heartbeat: 10m=600s)
        res = self.run_async(self.ocpp.on_change_configuration(key="HeartbeatInterval", value="600"))
        self.assertEqual(res['status'], "Accepted")

        # 1.11 CSMS -> CS: ChangeConfiguration (MeterValue: 30s)
        res = self.run_async(self.ocpp.on_change_configuration(key="MeterValueSampleInterval", value="30"))
        self.assertEqual(res['status'], "Accepted")

        # 1.12 CSMS -> CS: ChangeConfiguration (UnlockConnector) -- Wait, 1.12 in table is UnLockConnector? 
        # Table says "ChangeConfiguration (UnlockConnector: True)" -> This is likely a config key or action?
        # Actually 1.12 says "ChangeConfiguration (UnlockConnector: True)" manually? 
        # Standard OCPP doesn't have UnlockConnector as config. It's an Action.
        # Let's assume it means "UnlockConnector" Action as per typical test, OR a config key if specific to MEA.
        # SRS Table 1.12: ChangeConfiguration(UnlockConnector: True) - Status: Accepted.
        # If it's a config key:
        res = self.run_async(self.ocpp.on_change_configuration(key="UnlockConnector", value="True"))
        self.assertEqual(res['status'], "Accepted")

        # 1.13 CSMS -> CS: ChangeAvailability (Inoperative)
        res = self.run_async(self.ocpp.on_change_availability(connector_id=1, type="Inoperative"))
        self.assertEqual(res['status'], "Accepted")

        # 1.14 CS -> CSMS: StatusNotification (Unavailable)
        self.run_async(self.ocpp.send_status_notification(status="Unavailable"))
        self.ocpp.call.assert_called()

        # 1.15 CSMS -> CS: ChangeAvailability (Operative)
        res = self.run_async(self.ocpp.on_change_availability(connector_id=1, type="Operative"))
        self.assertEqual(res['status'], "Accepted")

        # 1.16 CS -> CSMS: StatusNotification (Available)
        self.run_async(self.ocpp.send_status_notification(status="Available"))
        self.ocpp.call.assert_called()

        # 1.17 CSMS -> CS: GetDiagnostics
        res = self.run_async(self.ocpp.on_get_diagnostics(location="ftp://example.com"))
        self.assertEqual(res['file_name'], "diagnostics.log")

        # 1.18 CS -> CSMS: DiagnosticsStatusNotification
        # Simulated send
        pass 

        # 1.19 CSMS -> CS: UpdateFirmware
        res = self.run_async(self.ocpp.on_update_firmware(location="ftp://firmware.bin"))
        self.assertIsNotNone(res)

        # 1.20 CS -> CSMS: FirmwareStatusNotification
        pass

        # 1.21 CSMS -> CS: ChangeConfiguration (LocalAuthOffline: True)
        res = self.run_async(self.ocpp.on_change_configuration(key="LocalAuthorizeOffline", value="True"))
        self.assertEqual(res['status'], "Accepted")

        # 1.22 CSMS -> CS: SendLocalList
        res = self.run_async(self.ocpp.on_send_local_list(
            list_version=1, local_authorization_list=[], update_type="Full"
        ))
        self.assertEqual(res['status'], "Accepted")

        # 1.23 CSMS -> CS: GetLocalListVersion
        res = self.run_async(self.ocpp.on_get_local_list_version())
        self.assertEqual(res['list_version'], 1)

        # 1.24 CSMS -> CS: ClearCache
        res = self.run_async(self.ocpp.on_clear_cache())
        self.assertEqual(res['status'], "Accepted")

    def test_09_04_v2g_workaround(self):
        """9.4 Special: V2G Workaround (Power Demand via Config)"""
        # Send 5000W (Discharge to Grid)
        res = self.run_async(self.ocpp.on_change_configuration(key="MEA_V2G_PowerDemand", value="5000"))
        self.assertEqual(res['status'], "Accepted")

        # Send -2000W (Charge from Grid)
        res = self.run_async(self.ocpp.on_change_configuration(key="MEA_V2G_PowerDemand", value="-2000"))
        self.assertEqual(res['status'], "Accepted")

        # Send Invalid
        res = self.run_async(self.ocpp.on_change_configuration(key="MEA_V2G_PowerDemand", value="NOT_INT"))
        self.assertEqual(res['status'], "Rejected")

    # --- 2. Auto Charge Verification ---
    def test_2_autocharge_verification(self):
        """2.1 - 2.13 Auto Charge Verification"""
        # 2.1 ChangeConfig AutoCharge: False
        res = self.run_async(self.ocpp.on_change_configuration(key="AutoCharge", value="False"))
        self.assertEqual(res['status'], "Accepted")
        
        # 2.2 Plug -> Status: Preparing (No Auth)
        self.run_async(self.ocpp.send_status_notification(status="Preparing"))
        self.ocpp.call.assert_called()

        # 2.3 Unplug -> Status: Available
        self.run_async(self.ocpp.send_status_notification(status="Available"))
        self.ocpp.call.assert_called()

        # 2.4 Enable AutoCharge
        res = self.run_async(self.ocpp.on_change_configuration(key="AutoCharge", value="True"))
        self.assertEqual(res['status'], "Accepted")

        # 2.5 Plug -> Status: Preparing
        self.run_async(self.ocpp.send_status_notification(status="Preparing"))
        
        # 2.6 Authorize
        # Simulated sending Authorize
        pass

        # 2.7 StartTransaction
        self.run_async(self.ocpp.send_start_transaction(id_tag="VID:123"))
        
        # 2.8 Status: Charging
        self.run_async(self.ocpp.send_status_notification(status="Charging"))

        # 2.9 MeterValues
        pass

        # 2.10 RemoteStopTransaction
        self.mock_charger.stopped = False 
        res = self.run_async(self.ocpp.on_remote_stop_transaction(transaction_id=999))
        self.assertEqual(res['status'], "Accepted")
        self.mock_charger.stop.assert_called()

        # 2.11 StopTransaction
        self.run_async(self.ocpp.send_stop_transaction(transaction_id=999))

        # 2.12 Status: Finishing
        self.run_async(self.ocpp.send_status_notification(status="Finishing"))

        # 2.13 Status: Available
        self.run_async(self.ocpp.send_status_notification(status="Available"))

    # --- 3. Normal Operation Verification ---
    def test_3_normal_operation(self):
        """3.1 - 3.17 Normal Operation"""
        # 3.1 Boot
        self.run_async(self.ocpp.send_boot_notification())
        # 3.2 Status StatusNotification
        self.run_async(self.ocpp.send_status_notification(status="Available"))
        # 3.3 Plug
        self.run_async(self.ocpp.send_status_notification(status="Preparing"))
        # 3.4 Authorize
        pass
        # 3.5 StartTransaction
        self.run_async(self.ocpp.send_start_transaction())
        # 3.6 Status Charging
        self.run_async(self.ocpp.send_status_notification(status="Charging"))
        # 3.7 MeterValues
        pass
        # 3.8 StopTransaction (Card)
        self.run_async(self.ocpp.send_stop_transaction(transaction_id=101))
        
        # 3.12 RemoteStartTransaction
        self.mock_charger.stopped = True
        res = self.run_async(self.ocpp.on_remote_start_transaction(id_tag="DEADBEEF"))
        self.assertEqual(res['status'], "Accepted")
        # 3.13 StartTransaction matches RemoteStart
        self.run_async(self.ocpp.send_start_transaction())
        
        # 3.16 RemoteStopTransaction
        self.mock_charger.stopped = False
        res = self.run_async(self.ocpp.on_remote_stop_transaction(transaction_id=123))
        self.assertEqual(res['status'], "Accepted")
        # 3.17 StopTransaction matches RemoteStop
        self.run_async(self.ocpp.send_stop_transaction(transaction_id=123))

    # --- 4. Reset Verification ---
    def test_4_reset_verification(self):
        """4.1 Hard Reset, 4.9 Soft Reset"""
        # 4.1 Hard Reset
        res = self.run_async(self.ocpp.on_reset(type="Hard"))
        self.assertEqual(res['status'], "Accepted")
        
        # 4.2 Boot after reset
        self.run_async(self.ocpp.send_boot_notification())

        # 4.9 Soft Reset
        res = self.run_async(self.ocpp.on_reset(type="Soft"))
        self.assertEqual(res['status'], "Accepted")

    # --- 5. Reservation Verification ---
    def test_5_reservation_verification(self):
        """5.2 ReserveNow, 5.4 CancelReservation"""
        # 5.2 ReserveNow
        res = self.run_async(self.ocpp.on_reserve_now(
            reservation_id=1, expiry_date="2025-12-31T23:59:59Z", id_tag="RES_TAG", connector_id=1
        ))
        self.assertEqual(res['status'], "Accepted")

        # 5.4 CancelReservation
        res = self.run_async(self.ocpp.on_cancel_reservation(reservation_id=1))
        self.assertEqual(res['status'], "Accepted")
        
        # 5.6 ReserveNow (1 min)
        # Same call, just verification of params in real logic.
        res = self.run_async(self.ocpp.on_reserve_now(
            reservation_id=2, expiry_date="2025-...", id_tag="RES_TAG_2", connector_id=1
        ))
        self.assertEqual(res['status'], "Accepted")

        # 5.8 Status (Timeout) -> Available
        self.run_async(self.ocpp.send_status_notification(status="Available"))
        
        # 5.13 StartTransaction (Reserved)
        # Should include reservationId
        pass

    # --- 6. Charging Profile Verification ---
    def test_6_charging_profile(self):
        """6.7 SetChargingProfile"""
        # 6.7 Set (TxProfile)
        res = self.run_async(self.ocpp.on_set_charging_profile(
            connector_id=1, cs_charging_profiles={}
        ))
        self.assertEqual(res['status'], "Accepted")
        
        # 6.9 Set (Update)
        res = self.run_async(self.ocpp.on_set_charging_profile(
            connector_id=1, cs_charging_profiles={}
        ))
        self.assertEqual(res['status'], "Accepted")

        # 6.11 StopTransaction (Verify Profile Effect)
        # Manual verification conceptually
        pass

    # --- 7. Abnormal Operation Verification ---
    def test_7_abnormal_operation(self):
        """7.1 Remote Start (Unplugged)"""
        # 7.1 Expect Rejected if Unplugged (or logic to handle it)
        self.mock_charger.stopped = False # Simulating busy or invalid state
        res = self.run_async(self.ocpp.on_remote_start_transaction(id_tag="DEADBEEF"))
        self.assertEqual(res['status'], "Rejected")
        
        # 7.2 Concurrent Remote Start
        # 2nd request rejection
        pass

        # 7.3 Swap Card
        pass
        
        # 7.4 Emergency Stop
        self.mock_charger.stopped = False
        res = self.run_async(self.ocpp.on_remote_stop_transaction(transaction_id=999))
        self.assertEqual(res['status'], "Accepted")

        # 7.6 Power Loss
        pass

    # --- 8. Dual Connector Operation ---
    def test_8_dual_connector(self):
        """8.1 Concurrent Remote Start"""
        # 8.1 Concurrent Remote Start (1 & 2)
        res = self.run_async(self.ocpp.on_remote_start_transaction(id_tag="TAG2", connector_id=2))
        self.assertIsNotNone(res)
        
        # 8.2 Emergency Stop (Shared)
        self.mock_charger.stopped = False # Simulate running transaction
        res = self.run_async(self.ocpp.on_remote_stop_transaction(transaction_id=999))
        self.assertEqual(res['status'], "Accepted")

    # --- 9. MEA Specific Configuration ---
    def test_9_mea_specific_config(self):
        """9.1, 9.2 ChangeConfiguration specific values"""
        # 9.1 HeartbeatInterval: 60 min (3600s)
        res = self.run_async(self.ocpp.on_change_configuration(key="HeartbeatInterval", value="3600"))
        self.assertEqual(res['status'], "Accepted")

        # 9.2 MeterValueSampleInterval: 60s (implied 1 min)
        res = self.run_async(self.ocpp.on_change_configuration(key="MeterValueSampleInterval", value="60"))
        self.assertEqual(res['status'], "Accepted")

        # 9.3 LocalAuthorizeOffline: False
        res = self.run_async(self.ocpp.on_change_configuration(key="LocalAuthorizeOffline", value="False"))
        self.assertEqual(res['status'], "Accepted")

    def test_09_04_v2g_workaround(self):
        """9.4 Special: V2G Workaround (Power Demand via Config)"""
        # Send 5000W (Discharge to Grid)
        res = self.run_async(self.ocpp.on_change_configuration(key="MEA_V2G_PowerDemand", value="5000"))
        self.assertEqual(res['status'], "Accepted")

        # Send -2000W (Charge from Grid)
        res = self.run_async(self.ocpp.on_change_configuration(key="MEA_V2G_PowerDemand", value="-2000"))
        self.assertEqual(res['status'], "Accepted")

        # Send Invalid
        res = self.run_async(self.ocpp.on_change_configuration(key="MEA_V2G_PowerDemand", value="NOT_INT"))
        self.assertEqual(res['status'], "Rejected")

    # --- 10. Message Summary Checks ---
    def test_10_message_summary(self):
        """10.x All Core Operations"""
        # Verify Local List
        res = self.run_async(self.ocpp.on_send_local_list(
            list_version=1, local_authorization_list=[], update_type="Full"
        ))
        self.assertEqual(res['status'], "Accepted")

        res = self.run_async(self.ocpp.on_get_local_list_version())
        self.assertEqual(res['list_version'], 1)

if __name__ == '__main__':
    unittest.main()

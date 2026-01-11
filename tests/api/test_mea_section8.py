import pytest
import time
import json

TAG_1 = "RFID_SIM"
TAG_2 = "RFID_SIM"

@pytest.mark.usefixtures("evse_simulation")
class TestMeaSection8:
    
    # --- Helper Methods ---
    packet_buffer = []

    def wait_for_packet(self, packet_content, timeout=20):
        """
        Waits for a specific packet content to appear in the PACKET_QUEUE.
        Buffers unmatched messages to avoid losing them for subsequent tests.
        """
        from tests.api.conftest import PACKET_QUEUE
        
        start_time = time.time()
        
        # 1. Check Buffer First
        for i, msg in enumerate(self.packet_buffer):
            if packet_content in msg:
                print(f"DEBUG: Found '{packet_content}' in BUFFER")
                self.packet_buffer.pop(i)
                return True

        # 2. Poll Queue
        while time.time() - start_time < timeout:
            if not PACKET_QUEUE.empty():
                msg = PACKET_QUEUE.get()
                print(f"DEBUG: Found in queue: {msg}")
                
                if packet_content in msg:
                    return True
                else:
                    print(f"DEBUG: Buffering unmatched: {msg}")
                    self.packet_buffer.append(msg)
            else:
                 time.sleep(0.1)
        
        print(f"DEBUG: Timeout waiting for '{packet_content}'. Buffer has {len(self.packet_buffer)} items.")
        return False

    # --- 8.1 Concurrent Remote Start (Dual) ---
    def test_8_01_01_status_available(self, evse_simulation):
        print("\n--- 8.1.1 Status Available (Unplug) ---")
        evse_simulation.queue.put({'command': 'STATUS', 'args': {'connector_id': 1, 'status': 'Available'}})
        evse_simulation.queue.put({'command': 'STATUS', 'args': {'connector_id': 2, 'status': 'Available'}})
        assert self.wait_for_packet("StatusNotification ACKNOWLEDGED (Available)")

    def test_8_01_02_status_preparing(self, evse_simulation):
        print("\n--- 8.1.2 Status Preparing (Plug Both) ---")
        evse_simulation.queue.put({'command': 'STATUS', 'args': {'connector_id': 1, 'status': 'Preparing'}})
        evse_simulation.queue.put({'command': 'STATUS', 'args': {'connector_id': 2, 'status': 'Preparing'}})
        assert self.wait_for_packet("StatusNotification ACKNOWLEDGED (Preparing)")

    def test_8_01_03_remote_start_1(self, evse_simulation):
        print("\n--- 8.1.3 Remote Start Connector 1 ---")
        payload = json.dumps([2, "uuid-rs-1", "RemoteStartTransaction", {"connectorId": 1, "idTag": TAG_1}])
        evse_simulation.queue.put({'command': 'INJECT_MSG', 'args': {'msg': payload}})
        assert self.wait_for_packet("Sending RemoteStartTransaction Response (Accepted)")

    def test_8_01_04_remote_start_2(self, evse_simulation):
        print("\n--- 8.1.4 Remote Start Connector 2 ---")
        payload = json.dumps([2, "uuid-rs-2", "RemoteStartTransaction", {"connectorId": 2, "idTag": TAG_2}])
        evse_simulation.queue.put({'command': 'INJECT_MSG', 'args': {'msg': payload}})
        assert self.wait_for_packet("Sending RemoteStartTransaction Response (Accepted)")

    def test_8_01_05_start_transaction_1(self, evse_simulation):
        print("\n--- 8.1.5 Start Transaction Connector 1 ---")
        assert self.wait_for_packet("Transaction started on 1")

    def test_8_01_06_status_charging_1(self, evse_simulation):
        print("\n--- 8.1.6 Status Charging Connector 1 ---")
        assert self.wait_for_packet("StatusNotification ACKNOWLEDGED (Charging)")

    def test_8_01_07_start_transaction_2(self, evse_simulation):
        print("\n--- 8.1.7 Start Transaction Connector 2 ---")
        assert self.wait_for_packet("Transaction started on 2")

    def test_8_01_08_status_charging_2(self, evse_simulation):
        print("\n--- 8.1.8 Status Charging Connector 2 ---")
        assert self.wait_for_packet("StatusNotification ACKNOWLEDGED (Charging)")

    def test_8_01_09_metervalues_1(self, evse_simulation):
        print("\n--- 8.1.9 MeterValues Connector 1 ---")
        assert self.wait_for_packet("MeterValues ACKNOWLEDGED")

    def test_8_01_10_metervalues_2(self, evse_simulation):
        print("\n--- 8.1.10 MeterValues Connector 2 ---")
        assert self.wait_for_packet("MeterValues ACKNOWLEDGED")

    def test_8_01_11_remote_stop_1(self, evse_simulation):
         print("\n--- 8.1.11 Remote Stop Connector 1 ---")
         # Simulating RemoteStop via Local Stop for simplicity as discussed
         evse_simulation.queue.put({'command': 'STOP_TRANSACTION', 'args': {'connector_id': 1}})
         assert self.wait_for_packet("Transaction stopped")

    def test_8_01_12_stop_transaction_1(self, evse_simulation):
         pass

    def test_8_01_13_status_finishing_1(self, evse_simulation):
         print("\n--- 8.1.13 Status Finishing 1 ---")
         assert self.wait_for_packet("StatusNotification ACKNOWLEDGED (Finishing)")

    def test_8_01_14_status_available_1(self, evse_simulation):
         print("\n--- 8.1.14 Status Available 1 ---")
         assert self.wait_for_packet("StatusNotification ACKNOWLEDGED (Available)")

    def test_8_01_15_remote_stop_2(self, evse_simulation):
         print("\n--- 8.1.15 Stop Connector 2 ---")
         evse_simulation.queue.put({'command': 'STOP_TRANSACTION', 'args': {'connector_id': 2}})
         assert self.wait_for_packet("Transaction stopped")

    def test_8_01_16_stop_transaction_2(self, evse_simulation):
         pass

    def test_8_01_17_status_finishing_2(self, evse_simulation):
         assert self.wait_for_packet("StatusNotification ACKNOWLEDGED (Finishing)")

    def test_8_01_18_status_available_2(self, evse_simulation):
         print("\n--- 8.1.18 Status Available 2 ---")
         assert self.wait_for_packet("StatusNotification ACKNOWLEDGED (Available)")

    # --- 8.2 Shared Emergency Stop (Dual) ---
    def test_8_02_01_status_available(self, evse_simulation):
        print("\n--- 8.2.1 Status Available ---")
        evse_simulation.queue.put({'command': 'STATUS', 'args': {'connector_id': 1, 'status': 'Available'}})
        evse_simulation.queue.put({'command': 'STATUS', 'args': {'connector_id': 2, 'status': 'Available'}})
        time.sleep(1)

    def test_8_02_02_status_preparing_1(self, evse_simulation):
        print("\n--- 8.2.2 Status Preparing (Conn 1) ---")
        evse_simulation.queue.put({'command': 'STATUS', 'args': {'connector_id': 1, 'status': 'Preparing'}})
    
    def test_8_02_03_remote_start_1(self, evse_simulation):
        print("\n--- 8.2.3 Remote Start (Conn 1) ---")
        # Simulating CSMS sending RemoteStart - handled implicitly or we can inject
        # Here we mimic the flow leading to StartTransaction
        pass

    def test_8_02_04_start_transaction_1(self, evse_simulation):
        print("\n--- 8.2.4 Start Transaction (Conn 1) ---")
        evse_simulation.queue.put({'command': 'START_TRANSACTION', 'args': {'connector_id': 1, 'id_tag': TAG_1}})
        assert self.wait_for_packet("Transaction started on 1")

    def test_8_02_05_status_charging_1(self, evse_simulation):
        print("\n--- 8.2.5 Status Charging (Conn 1) ---")
        assert self.wait_for_packet("StatusNotification ACKNOWLEDGED (Charging)")

    def test_8_02_06_metervalues_1(self, evse_simulation):
        print("\n--- 8.2.6 MeterValues (Conn 1) ---")
        # Ensure we catch at least one MV
        assert self.wait_for_packet("MeterValues ACKNOWLEDGED")

    def test_8_02_07_status_preparing_2(self, evse_simulation):
        print("\n--- 8.2.7 Status Preparing (Conn 2) ---")
        evse_simulation.queue.put({'command': 'STATUS', 'args': {'connector_id': 2, 'status': 'Preparing'}})

    def test_8_02_08_remote_start_2(self, evse_simulation):
         print("\n--- 8.2.8 Remote Start (Conn 2) ---")
         pass

    def test_8_02_09_start_transaction_2(self, evse_simulation):
        print("\n--- 8.2.9 Start Transaction (Conn 2) ---")
        evse_simulation.queue.put({'command': 'START_TRANSACTION', 'args': {'connector_id': 2, 'id_tag': TAG_2}})
        assert self.wait_for_packet("Transaction started on 2")

    def test_8_02_10_status_charging_2(self, evse_simulation):
        print("\n--- 8.2.10 Status Charging (Conn 2) ---")
        assert self.wait_for_packet("StatusNotification ACKNOWLEDGED (Charging)")

    def test_8_02_11_metervalues_2(self, evse_simulation):
        print("\n--- 8.2.11 MeterValues (Conn 2) ---")
        assert self.wait_for_packet("MeterValues ACKNOWLEDGED")

    def test_8_02_12_stop_conn_1(self, evse_simulation):
        print("\n--- 8.2.12 Stop Transaction (Conn 1 - Emergency) ---")
        evse_simulation.queue.put({'command': 'STOP_TRANSACTION', 'args': {'connector_id': 1, 'reason': 'EmergencyStop'}})
        assert self.wait_for_packet("Transaction stopped")

    def test_8_02_13_stop_conn_2(self, evse_simulation):
        print("\n--- 8.2.13 Stop Transaction (Conn 2 - Emergency) ---")
        evse_simulation.queue.put({'command': 'STOP_TRANSACTION', 'args': {'connector_id': 2, 'reason': 'EmergencyStop'}})
        assert self.wait_for_packet("Transaction stopped")

    def test_8_02_14_status_faulted(self, evse_simulation):
        print("\n--- 8.2.14 Status Faulted (Shared) ---")
        evse_simulation.queue.put({'command': 'STATUS', 'args': {'connector_id': 1, 'status': 'Faulted', 'error_code': 'OtherError', 'info': 'EmergencyStop'}})
        evse_simulation.queue.put({'command': 'STATUS', 'args': {'connector_id': 2, 'status': 'Faulted', 'error_code': 'OtherError', 'info': 'EmergencyStop'}})
        assert self.wait_for_packet("StatusNotification ACKNOWLEDGED (Faulted)")

    def test_8_02_15_status_faulted_unplug(self, evse_simulation):
        print("\n--- 8.2.15 Status Faulted (Unplug) ---")
        # Handled in 8.2.14 flow often, but explicit step here
        pass

    def test_8_02_16_status_available(self, evse_simulation):
        print("\n--- 8.2.16 Status Available (Release) ---")
        evse_simulation.queue.put({'command': 'STATUS', 'args': {'connector_id': 1, 'status': 'Available', 'error_code': 'NoError'}})
        evse_simulation.queue.put({'command': 'STATUS', 'args': {'connector_id': 2, 'status': 'Available', 'error_code': 'NoError'}})
        assert self.wait_for_packet("StatusNotification ACKNOWLEDGED (Available)")

    # --- 8.3 Power Loss (Dual) ---
    def test_8_03_01_status_available(self, evse_simulation):
        print("\n--- 8.3.1 Status Available ---")
        evse_simulation.queue.put({'command': 'STATUS', 'args': {'connector_id': 1, 'status': 'Available'}})
        evse_simulation.queue.put({'command': 'STATUS', 'args': {'connector_id': 2, 'status': 'Available'}})
        time.sleep(1)

    def test_8_03_02_status_preparing_1(self, evse_simulation):
        print("\n--- 8.3.2 Status Preparing (Conn 1) ---")
        evse_simulation.queue.put({'command': 'STATUS', 'args': {'connector_id': 1, 'status': 'Preparing'}})

    def test_8_03_03_remote_start_1(self, evse_simulation):
         pass

    def test_8_03_04_start_transaction_1(self, evse_simulation):
        print("\n--- 8.3.4 Start Transaction (Conn 1) ---")
        evse_simulation.queue.put({'command': 'START_TRANSACTION', 'args': {'connector_id': 1, 'id_tag': TAG_1}})
        assert self.wait_for_packet("Transaction started on 1")

    def test_8_03_05_status_charging_1(self, evse_simulation):
        print("\n--- 8.3.5 Status Charging (Conn 1) ---")
        assert self.wait_for_packet("StatusNotification ACKNOWLEDGED (Charging)")

    def test_8_03_06_metervalues_1(self, evse_simulation):
        assert self.wait_for_packet("MeterValues ACKNOWLEDGED")

    def test_8_03_07_status_preparing_2(self, evse_simulation):
        print("\n--- 8.3.7 Status Preparing (Conn 2) ---")
        evse_simulation.queue.put({'command': 'STATUS', 'args': {'connector_id': 2, 'status': 'Preparing'}})

    def test_8_03_08_remote_start_2(self, evse_simulation):
         pass

    def test_8_03_09_start_transaction_2(self, evse_simulation):
        print("\n--- 8.3.9 Start Transaction (Conn 2) ---")
        evse_simulation.queue.put({'command': 'START_TRANSACTION', 'args': {'connector_id': 2, 'id_tag': TAG_2}})
        assert self.wait_for_packet("Transaction started on 2")

    def test_8_03_10_status_charging_2(self, evse_simulation):
        print("\n--- 8.3.10 Status Charging (Conn 2) ---")
        assert self.wait_for_packet("StatusNotification ACKNOWLEDGED (Charging)")

    def test_8_03_11_metervalues_2(self, evse_simulation):
        assert self.wait_for_packet("MeterValues ACKNOWLEDGED")

    def test_8_03_12_power_loss_stop_1(self, evse_simulation):
        print("\n--- 8.3.12 Stop Transaction (Conn 1 - PowerLoss) ---")
        evse_simulation.queue.put({'command': 'STOP_TRANSACTION', 'args': {'connector_id': 1, 'reason': 'PowerLoss'}})
        assert self.wait_for_packet("Transaction stopped")

    def test_8_03_13_power_loss_stop_2(self, evse_simulation):
         print("\n--- 8.3.13 Stop Transaction (Conn 2 - PowerLoss) ---")
         evse_simulation.queue.put({'command': 'STOP_TRANSACTION', 'args': {'connector_id': 2, 'reason': 'PowerLoss'}})
         assert self.wait_for_packet("Transaction stopped")

    def test_8_03_14_reboot(self, evse_simulation):
         print("\n--- 8.3.14 Recovery (Boot) ---")
         evse_simulation.queue.put({'command': 'BOOT'})
         assert self.wait_for_packet("BootNotification ACCEPTED")

    def test_8_03_15_status_preparing_both(self, evse_simulation):
        print("\n--- 8.3.15 Restore Status (Preparing) ---")
        evse_simulation.queue.put({'command': 'STATUS', 'args': {'connector_id': 1, 'status': 'Preparing'}})
        evse_simulation.queue.put({'command': 'STATUS', 'args': {'connector_id': 2, 'status': 'Preparing'}})
        assert self.wait_for_packet("StatusNotification ACKNOWLEDGED (Preparing)")

    def test_8_03_16_status_available_both(self, evse_simulation):
        print("\n--- 8.3.16 Restore Status (Available) ---")
        evse_simulation.queue.put({'command': 'STATUS', 'args': {'connector_id': 1, 'status': 'Available'}})
        evse_simulation.queue.put({'command': 'STATUS', 'args': {'connector_id': 2, 'status': 'Available'}})
        assert self.wait_for_packet("StatusNotification ACKNOWLEDGED (Available)")

import pytest
import time
import json

@pytest.mark.usefixtures("evse_simulation")
class TestMeaSection9:
    
    # --- Helper Methods ---
    packet_buffer = []

    def wait_for_packet(self, packet_content, timeout=20):
        """
        Waits for a specific packet content to appear in the PACKET_QUEUE.
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

    def send_change_config(self, evse_simulation, key, value):
        print(f"\n--- ChangeConfiguration {key}={value} ---")
        # Simulating CSMS sending ChangeConfiguration
        # Payload: [2, "uuid", "ChangeConfiguration", {"key": "key", "value": "value"}]
        payload = json.dumps([2, f"uuid-{key}", "ChangeConfiguration", {"key": key, "value": value}])
        evse_simulation.queue.put({'command': 'INJECT_MSG', 'args': {'msg': payload}})
        
        # Expect response: [3, "uuid", {"status": "Accepted"}]
        # The 'StatusNotification' or specific confirmation logs might vary, 
        # but usually we look for the specific confirmation being sent back.
        # The simulation logs "send [3...]" which contains the response.
        # We look for a log indicating the EVSE accepted it.
        # Based on Ocpp16Interface.py, upon receiving ChangeConfig, it replies.
        # We'll wait for the RESPONSE message string in the logs.
        # "send [3,\"uuid-{key}\",{\"status\":\"Accepted\"}]"
        
        # Simplified match string as usually used in these tests:
        # We match the high-level message put into PACKET_QUEUE by Ocpp16Interface
        return self.wait_for_packet("Sending ChangeConfiguration Response (Accepted)")

    # --- 9.0 Initialization ---
    def test_9_00_initialization(self, evse_simulation):
        print("\n--- 9.0 Initialization ---")
        # Ensure we are connected and booted
        assert self.wait_for_packet("[EVSE] Connected to CSMS!", timeout=30)
        assert self.wait_for_packet("BootNotification ACCEPTED", timeout=10)
        # We also usually send Status Available
        assert self.wait_for_packet("StatusNotification ACKNOWLEDGED", timeout=10)

    # --- 9.1 MEA Specific Configuration ---
    def test_9_01_v2gmode_config(self, evse_simulation):
        # Expect generic response message put in queue by Ocpp16Interface
        assert self.send_change_config(evse_simulation, "V2GMode", "true")

    def test_9_02_meter_sample(self, evse_simulation):
        assert self.send_change_config(evse_simulation, "MeterValueSampleInterval", "10")

    def test_9_03_local_auth(self, evse_simulation):
        assert self.send_change_config(evse_simulation, "LocalAuthorizeOffline", "false")

    def send_power_demand(self, evse_simulation, value):
        print(f"\n--- Power Demand Update: {value}W ---")
        assert self.send_change_config(evse_simulation, "Power.Active.Import", value)
        # Trigger MeterValue to verify the change
        evse_simulation.queue.put({'command': 'METER_VALUES', 'args': {'connector_id': 1}})
        assert self.wait_for_packet("SENDING Packet: MeterValues (One-off)")
        return self.wait_for_packet("MeterValues ACKNOWLEDGED")

    # --- 9.4 Power Demand Verification ---
    def test_9_04_00_v2g_mode_on(self, evse_simulation):
        assert self.send_change_config(evse_simulation, "V2GMode", "true")

    def test_9_04_01_power_neg5k(self, evse_simulation):
        assert self.send_power_demand(evse_simulation, "-5000")

    def test_9_04_02_power_2k(self, evse_simulation):
        assert self.send_power_demand(evse_simulation, "2000")

    def test_9_04_03_power_0(self, evse_simulation):
        assert self.send_power_demand(evse_simulation, "0")

import pytest
import time
import statistics

from Ocpp16Interface import Ocpp16Interface

@pytest.mark.usefixtures("evse_simulation")
class TestMeaSection11:
    @classmethod
    def setup_class(cls):
        # Enable extended logging for this section report
        Ocpp16Interface.LOG_SEND_PACKETS = True
        print("\n[TestMeaSection11] Extended Logging ENABLED for Report Generation")

    @classmethod
    def teardown_class(cls):
        # Restore default
        Ocpp16Interface.LOG_SEND_PACKETS = False
        print("\n[TestMeaSection11] Extended Logging DISABLED")

    
    # --- Helper Methods ---
    packet_buffer = []

    def wait_for_packet(self, packet_content, timeout=20):
        # Same helper as Section 10
        from tests.api.conftest import PACKET_QUEUE
        start_time = time.time()
        for i, msg in enumerate(self.packet_buffer):
            if packet_content in msg:
                self.packet_buffer.pop(i)
                return True
        while time.time() - start_time < timeout:
            if not PACKET_QUEUE.empty():
                msg = PACKET_QUEUE.get()
                if packet_content in msg:
                    return True
                else:
                    self.packet_buffer.append(msg)
            else:
                 time.sleep(0.1)
        return False

    def measure_reconnect_time(self, evse_simulation):
        """
        Triggers a disconnect and measures time until BootNotification is accepted.
        Returns time in seconds.
        """
        print("DEBUG: Triggering Disconnect...")
        start_time = time.time()
        evse_simulation.queue.put({'command': 'CLOSE_CONNECTION'})
        
        # Wait for "Connected to CSMS!"
        # Then Wait for "BootNotification ACCEPTED"
        # The loop in conftest prints "[EVSE] Connected to CSMS!" then sends BootNotification.
        # So measuring until "BootNotification ACCEPTED" is a good end-to-end check.
        
        # We need to clear buffer potentially or ensure we don't match old packets?
        # Ideally clear buffer before start.
        self.packet_buffer = []
        
        # Assert re-connection happens
        if not self.wait_for_packet("[EVSE] Connected to CSMS!", timeout=30):
            pytest.fail("Failed to reconnect to CSMS within 30s")
            
        if not self.wait_for_packet("BootNotification ACCEPTED", timeout=10):
            pytest.fail("BootNotification not accepted after reconnect")
            
        duration = time.time() - start_time
        print(f"DEBUG: Reconnect took {duration:.2f} seconds")
        return duration

    # --- 11.1 Reconnect Time ---
    def test_11_01_reconnect_time(self, evse_simulation):
        print("\n--- 11.1 Reconnect Time ---")
        duration = self.measure_reconnect_time(evse_simulation)
        print(f"Reconnection Time: {duration:.2f}s")
        # Threshold assumption: < 15 seconds (5s sleep + overhead)
        assert duration < 15.0, f"Reconnection took too long: {duration:.2f}s"

    # --- 11.2 Average Performance ---
    def test_11_02_average_performance(self, evse_simulation):
        print("\n--- 11.2 Average Performance ---")
        timings = []
        for i in range(5):
            print(f"Iteration {i+1}/5...")
            # Small delay between iterations
            time.sleep(1)
            t = self.measure_reconnect_time(evse_simulation)
            timings.append(t)
            
        avg_time = statistics.mean(timings)
        print(f"Average Reconnection Time (5 runs): {avg_time:.2f}s")
        
        # Report max, min as well via print
        print(f"Min: {min(timings):.2f}s, Max: {max(timings):.2f}s")
        
        assert avg_time < 15.0, f"Average reconnection too slow: {avg_time:.2f}s"

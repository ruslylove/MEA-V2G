from RFIDReaderInterface import RFIDReaderInterface
import threading
import time

class RFIDSim(RFIDReaderInterface):
    """
    Simulation of an RFID Reader.
    Can be used to programmatically simulate swipes.
    """

    def __init__(self):
        self.callback = None
        self.running = False
        self._thread = None
        
    def start(self, callback):
        """
        Starts the RFID simulation.
        """
        self.callback = callback
        self.running = True
        # For simulation, we don't necessarily need a background thread unless we want to monitor stdin or something.
        # But to be safe and extensible, we might just have it ready.
        # For now, we rely on duplicate `simulate_scan` calls or external triggers.
        # If we wanted keyboard input, we'd need a thread:
        # self._thread = threading.Thread(target=self._input_loop)
        # self._thread.daemon = True
        # self._thread.start()
        print("RFIDSim started. Call simulate_scan(id_tag) to trigger.")

    def stop(self):
        """
        Stops the RFID simulation.
        """
        self.running = False
        print("RFIDSim stopped.")

    def simulate_scan(self, id_tag="RFID_SIM"):
        """
        Simulates scanning a card.
        """
        if self.running and self.callback:
            self.callback(id_tag)
        else:
            print("RFIDSim ignored scan (not running or no callback)")

import unittest
from unittest.mock import MagicMock
import sys
import os

# Add parent directory to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from RFIDSim import RFIDSim

class TestRFIDSim(unittest.TestCase):
    def test_start_stop(self):
        reader = RFIDSim()
        callback = MagicMock()
        
        reader.start(callback)
        self.assertTrue(reader.running)
        self.assertEqual(reader.callback, callback)
        
        reader.stop()
        self.assertFalse(reader.running)

    def test_simulate_scan(self):
        reader = RFIDSim()
        callback = MagicMock()
        
        # Should not work before start
        reader.simulate_scan("TAG1")
        callback.assert_not_called()
        
        reader.start(callback)
        reader.simulate_scan("TAG1")
        callback.assert_called_with("TAG1")
        
        # Test default
        callback.reset_mock()
        reader.simulate_scan()
        callback.assert_called_with("RFID_SIM")
        
        # Should not work after stop
        reader.stop()
        callback.reset_mock()
        reader.simulate_scan("TAG2")
        callback.assert_not_called()

if __name__ == '__main__':
    unittest.main()

import sys
import os
import unittest
import time

# Add parent directory to path to import modules
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from Charger import Charger

class TestCharger(unittest.TestCase):
    def setUp(self):
        self.charger = Charger()

    def test_initial_state(self):
        self.assertTrue(self.charger.stopped)
        self.assertEqual(self.charger.evse_present_voltage, 0)
        self.assertEqual(self.charger.evse_present_current, 0)

    def test_start_stop(self):
        self.charger.start()
        self.assertFalse(self.charger.stopped)
        
        self.charger.stop()
        self.assertTrue(self.charger.stopped)
        self.assertEqual(self.charger.ev_target_voltage, 0)
        self.assertEqual(self.charger.ev_target_current, 0)

    def test_set_evse_capabilities(self):
        self.charger.setEvseMaxCurrent(100)
        self.assertEqual(self.charger.getEvseMaxCurrent(), 100)
        
        self.charger.setEvseMinCurrent(6)
        self.assertEqual(self.charger.getEvseMinCurrent(), 6)
        
        self.charger.setEvseMaxVoltage(500)
        self.assertEqual(self.charger.getEvseMaxVoltage(), 500)
        
        self.charger.setEvseMaxPower(20000)
        self.assertEqual(self.charger.getEvseMaxPower(), 20000)

    def test_limit_checks(self):
        self.charger.setEvseMaxVoltage(400)
        self.charger.setEvseMinVoltage(10)
        self.assertTrue(self.charger.isVoltageLimitExceeded(401))
        self.assertTrue(self.charger.isVoltageLimitExceeded(9))
        self.assertFalse(self.charger.isVoltageLimitExceeded(200))

        self.charger.setEvseMaxCurrent(32)
        self.charger.setEvseMinCurrent(0)
        self.assertTrue(self.charger.isCurrentLimitExceeded(33))
        self.assertFalse(self.charger.isCurrentLimitExceeded(16))

        self.charger.setEvseMaxPower(10000)
        self.assertTrue(self.charger.isPowerLimitExceeded(10001))
        self.assertFalse(self.charger.isPowerLimitExceeded(5000))

    def test_target_setting_validation(self):
        self.charger.setEvseMaxVoltage(400)
        self.charger.setEvseMaxCurrent(32)

        # Valid targets
        self.assertTrue(self.charger.setEvTargetVoltage(380))
        self.assertTrue(self.charger.setEvTargetCurrent(16))
        
        # Invalid targets
        self.assertFalse(self.charger.setEvTargetVoltage(401))
        self.assertFalse(self.charger.setEvTargetCurrent(33))

    def test_ramp_up_simulation(self):
        """
        Verify that present voltage/current approaches target voltage/current 
        based on delta values over time.
        """
        self.charger.setEvseMaxVoltage(400)
        self.charger.setEvseMaxCurrent(32)
        self.charger.setEvseDeltaVoltage(10) # 10V per ms (if we assume logic uses similar scale, but code uses time delta)
        self.charger.setEvseDeltaCurrent(1)
        
        self.charger.start()
        
        # Set target
        self.charger.setEvTargetVoltage(100)
        
        # Simulate wait
        time.sleep(0.1) # 100ms
        
        # This test depends heavily on the specific time-based implementation in Charger.py
        # validation logic. We just check if it increased.
        v = self.charger.getEvsePresentVoltage()
        self.assertGreater(v, 0)
        self.assertLessEqual(v, 100)

if __name__ == '__main__':
    unittest.main()

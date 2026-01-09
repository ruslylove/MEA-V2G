import pytest
import asyncio
import threading
import websockets
import ssl
import sys
import os
import logging
import time

# Add root directory to path to allow imports
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from Ocpp16Interface import Ocpp16Interface, PACKET_QUEUE
from Charger import Charger

# Configuration
# Best guess based on API URL. 
# Note: check_ws_connection.py failed to verify, but we must try one.
# Found in tests/system/test_mea_live.py
WS_URL = "wss://ocpp.measandbox.com:2930/EV/Srv/JSON/1.6/rddQC4000001"

CHARGEPOINT_ID = "rddQC4000001"

logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger('EVSE_Sim')

class EvseSimulator:
    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.stop_event = asyncio.Event()
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.connected = False
        
    def start(self):
        self.thread.start()
        # Wait a bit for connection
        time.sleep(2)
        
    def stop(self):
        # We can't easily stop the loop from outside without access
        # But daemon thread will allow exit.
        pass

    def _run_loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self._main())

    async def _main(self):
        LOGGER.info(f"Connecting to {WS_URL}")
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        
        while True:
            try:
                # Handle wss vs ws
                extra_args = {}
                if WS_URL.startswith('wss'):
                     extra_args['ssl'] = ssl_context
                
                async with websockets.connect(
                    WS_URL, 
                    subprotocols=['ocpp1.6'],
                    ping_interval=20,
                    **extra_args
                ) as ws:
                    PACKET_QUEUE.put("[EVSE] Connected to CSMS!")
                    LOGGER.info("Connected to CSMS!")
                    self.connected = True
                    
                    charger = Charger()
                    # Setup default charger values
                    charger.setEvseMaxCurrent(32)
                    charger.setEvseMaxVoltage(230)
                    charger.start()
                    
                    cp = Ocpp16Interface(CHARGEPOINT_ID, ws, charger)
                    
                    # Start the CP processing loop in a background task so we can send messages
                    # But ocpp 0.x/1.x start() is often the loop itself. 
                    # We need to run start() concurrently.
                    block_task = asyncio.create_task(cp.start())
                    
                    # Wait a moment for start to initialize
                    await asyncio.sleep(1)
                    
                    # Send BootNotification
                    PACKET_QUEUE.put("[EVSE] Sending BootNotification...")
                    LOGGER.info("Sending BootNotification...")
                    await cp.send_boot_notification()
                    
                    # Send StatusNotification
                    PACKET_QUEUE.put("[EVSE] Sending StatusNotification (Available)...")
                    LOGGER.info("Sending StatusNotification (Available)...")
                    await cp.send_status_notification(connector_id=0, status="Available")
                    
                    # Send StatusNotification for Connector 1
                    PACKET_QUEUE.put("[EVSE] Sending StatusNotification (Connector 1: Available)...")
                    LOGGER.info("Sending StatusNotification (Connector 1: Available)...")
                    await cp.send_status_notification(connector_id=1, status="Available")

                    # Authorize RFID_SIM
                    PACKET_QUEUE.put("[EVSE] Sending Authorize (RFID_SIM)...")
                    LOGGER.info("Sending Authorize (RFID_SIM)...")
                    await cp.send_authorize(id_tag="RFID_SIM")
                    
                    # Send StatusNotification (Preparing) for Connector 1
                    PACKET_QUEUE.put("[EVSE] Sending StatusNotification (Connector 1: Preparing)...")
                    LOGGER.info("Sending StatusNotification (Connector 1: Preparing)...")
                    await cp.send_status_notification(connector_id=1, status="Preparing")

                    # Send Heartbeat
                    PACKET_QUEUE.put("[EVSE] Sending Heartbeat...")
                    await cp.send_heartbeat()
                    
                    # Wait on the connection loop
                    await block_task
            except Exception as e:
                LOGGER.error(f"Connection failed: {e}")
                self.connected = False
                await asyncio.sleep(5) # Retry interval

@pytest.fixture(scope="session", autouse=True)
def evse_simulation():
    """
    Starts matching EVSE simulation in background for the API tests.
    """
    LOGGER.info("Starting EVSE Simulation...")
    print(f"DEBUG: conftest PACKET_QUEUE ID: {id(PACKET_QUEUE)}")
    sim = EvseSimulator()
    sim.start()
    yield sim
    sim.stop()

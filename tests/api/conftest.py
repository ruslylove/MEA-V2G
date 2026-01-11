import pytest
import asyncio
import threading
import websockets
import ssl
import sys
import os
import logging
import time
import queue
import json

# Add root directory to path to allow imports
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from Ocpp16Interface import Ocpp16Interface, PACKET_QUEUE, ChargePointStatus
from Charger import Charger

# Configuration
WS_URL = "wss://ocpp.measandbox.com:2930/EV/Srv/JSON/1.6/rddQC4000001"
CHARGEPOINT_ID = "rddQC4000001"

logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger('EVSE_Sim')

# Queue for sending commands to EVSE (Test -> EVSE)
SIM_COMMAND_QUEUE = queue.Queue()

class EvseSimulator:
    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.stop_event = asyncio.Event()
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.connected = False
        self.cp = None
        
    def start(self):
        self.thread.start()
        # Wait a bit for connection
        time.sleep(2)
        
    def stop(self):
        LOGGER.info("Stopping EVSE Simulation...")
        self.stop_event.set()
        # Create task to close CP connection
        if self.cp:
            asyncio.run_coroutine_threadsafe(self.cp._connection.close(), self.loop)
        
        # self.thread.join(timeout=2) # blocking join might hang if loop is stuck


    def _run_loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self._main())

    async def _process_commands(self):
        """
        Background task to process commands from SIM_COMMAND_QUEUE.
        """
        LOGGER.info("Command processing loop started.")
        while True:
            # Wait for connection
            if not self.connected or not self.cp:
                await asyncio.sleep(0.1)
                continue

            try:
                # Non-blocking get from thread-safe queue
                # We need to poll because we are in asyncio but reading a sync queue
                try:
                    cmd_data = SIM_COMMAND_QUEUE.get_nowait()
                except queue.Empty:
                    await asyncio.sleep(0.1)
                    continue

                command = cmd_data.get('command')
                args = cmd_data.get('args', {})
                
                LOGGER.info(f"Processing Command: {command} with args: {args}")
                
                if not self.cp:
                    LOGGER.warning("Command ignored: CP not initialized.")
                    continue

                if command == 'STATUS':
                    # args: connector_id, status, error_code, info
                    await self.cp.send_status_notification(**args)
                    
                elif command == 'AUTHORIZE':
                    # args: id_tag
                    await self.cp.send_authorize(**args)
                
                elif command == 'START_TRANSACTION':
                    # args: connector_id, id_tag
                    await self.cp.start_transaction(**args)
                    
                elif command == 'STOP_TRANSACTION':
                    # args: connector_id, reason
                    await self.cp.stop_transaction(**args)
                    
                elif command == 'METER_VALUES':
                    # args: connector_id, transaction_id (optional, looks up internally)
                    # Force a single meter value
                    # We reuse internal method if possible or create new one?
                    # Ocpp16Interface has send_meter_values_one_off hardcoded to con 1.
                    # Ideally we fix that or just use the logic here.
                    await self.cp.send_meter_values_one_off(connector_id=args.get('connector_id', 1)) 

                elif command == 'BOOT':
                     await self.cp.send_boot_notification()

                elif command == 'HEARTBEAT':
                     await self.cp.send_heartbeat()

                elif command == 'OFFLINE':
                     self.cp.go_offline()

                elif command == 'RECONNECT':
                     asyncio.create_task(self.cp.go_online())

                elif command == 'INJECT_MSG':
                     # args: msg (string)
                     msg = args.get('msg')
                     if msg:
                         LOGGER.info(f"Injecting CSMS Message: {msg}")
                         await self.cp.route_message(msg)

                elif command == 'CLOSE_CONNECTION':
                     LOGGER.info("Forcing Connection Close...")
                     if self.cp and self.cp._connection:
                         await self.cp._connection.close()
                         # The loop in _main will catch this (ConnectionClosed) and restart.

            except Exception as e:
                LOGGER.error(f"Error processing command: {e}")

    async def _main(self):
        LOGGER.info(f"Connecting to {WS_URL}")
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        
        while True:
            try:
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
                    charger.setEvseMaxCurrent(32)
                    charger.setEvseMaxVoltage(230)
                    charger.start()
                    
                    self.cp = Ocpp16Interface(CHARGEPOINT_ID, ws, charger)
                    
                    # Start command processor
                    asyncio.create_task(self._process_commands())
                    
                    # Start the CP processing loop
                    block_task = asyncio.create_task(self.cp.start())
                    
                    await asyncio.sleep(1)
                    
                    # Initial Sequence (as before)
                    # NOTE: We keep this for Section 1 tests compat, but Section 2 might want to control it?
                    # Actually, Section 1 tests EXPECT this initial sequence. Section 2 can adapt.
                    
                    PACKET_QUEUE.put("[EVSE] Sending BootNotification...")
                    await self.cp.send_boot_notification()
                    
                    PACKET_QUEUE.put("[EVSE] Sending StatusNotification (Available)...")
                    await self.cp.send_status_notification(connector_id=0, status="Available")
                    
                    PACKET_QUEUE.put("[EVSE] Sending StatusNotification (Connector 1: Available)...")
                    await self.cp.send_status_notification(connector_id=1, status="Available")

                    # REMOVING the hardcoded Authorize/Preparing from conftest because Section 2 needs
                    # to start from a clean state (Unplugged/Available) or control the Plug event.
                    # IF I remove it, I might break Section 1 tests which relied on it?
                    # Section 1 Test 1.1/1.2 just checks Boot/Status. 
                    # Test 1.1 doesn't seem to check 'Preparing'.
                    # Let's KEEP Valid Card Authorize for connectivity check but maybe not shift to Preparing?
                    # Actually, looking at the previous run logs, the tests seemed fine with whatever happened.
                    # Best approach: Keep it minimal. Just Available.
                    # But wait, the original file had Authorize + Preparing.
                    # If I remove them, I must ensure Section 1 tests don't fail waiting for them if they did.
                    # Section 1 tests 1.1 and 1.2 check for Boot and Status.
                    # Let's keep Authorize for connectivity check but SKIP 'Preparing' so we stay 'Available'.
                    # Section 2 starts with "Config: AutoCharge=False", "Plug (Preparing)".
                    # So staying 'Available' is perfect for Section 2.
                    
                    # PACKET_QUEUE.put("[EVSE] Sending Authorize (RFID_SIM)...")
                    # await self.cp.send_authorize(id_tag="RFID_SIM")
                    # PACKET_QUEUE.put("[EVSE] Sending StatusNotification (Connector 1: Preparing)...")
                    # await self.cp.send_status_notification(connector_id=1, status="Preparing")

                    # Heartbeat
                    PACKET_QUEUE.put("[EVSE] Sending Heartbeat...")
                    await self.cp.send_heartbeat()
                    
                    await block_task
            except Exception as e:
                LOGGER.error(f"Connection failed: {e}")
                self.connected = False
                await asyncio.sleep(5)

@pytest.fixture(scope="session", autouse=True)
def evse_simulation():
    """
    Starts matching EVSE simulation in background for the API tests.
    """
    LOGGER.info("Starting EVSE Simulation...")
    sim = EvseSimulator()
    sim.queue = SIM_COMMAND_QUEUE
    sim.start()
    yield sim
    sim.stop()

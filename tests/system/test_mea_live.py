import asyncio
import websockets
import sys
import os
import logging
from datetime import datetime

# Add parent directory to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from Ocpp16Interface import Ocpp16Interface
from ocpp.v16 import call

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger('LiveTest')

CP_ID = "rddQC4000001"
CSMS_URL = f"ws://ocpp.measandbox.com:2931/EV/Srv/JSON/1.6/{CP_ID}"

class MockCharger:
    def __init__(self):
        self.stopped = True
    def stop(self):
        logger.info("Charger stopped.")
    def start_session(self):
        logger.info("Charger started session.")

async def main():
    logger.info(f"Connecting to {CSMS_URL}...")
    try:
        async with websockets.connect(
            CSMS_URL,
            subprotocols=['ocpp1.6'],
            ping_interval=None  # Disable auto-ping if problematic, or keep default
        ) as ws:
            logger.info("Connected!")

            # Initialize Wrapper
            charger_mock = MockCharger()
            cp = Ocpp16Interface(CP_ID, ws, charger_mock)

            # Start the background listening task
            # In a real app, this runs forever. Here we run it as a task we can cancel.
            # However, failing to run start() means we won't receive responses.
            # Ocpp16Interface.start() is blocking loop. So we need to schedule it.
            
            # Since standard ocpp lib's start() is a loop, we run it in background
            loop_task = asyncio.create_task(cp.start())

            # Give it a moment to handshake if needed
            await asyncio.sleep(1)

            # 1. Send BootNotification
            logger.info("Sending BootNotification...")
            boot_resp = await cp.send_boot_notification()
            logger.info(f"BootResponse: {boot_resp}")

            # 2. Send StatusNotification
            logger.info("Sending StatusNotification (Available)...")
            await cp.send_status_notification(status="Available")
            logger.info("StatusNotification sent.")
            
            # 3. Send Heartbeat
            logger.info("Sending Heartbeat...")
            hb_resp = await cp.call(call.HeartbeatPayload())
            logger.info(f"HeartbeatResponse: {hb_resp}")

            logger.info("Test Complete. Closing connection...")
            # Cancel the loop
            loop_task.cancel()
            try:
                await loop_task
            except asyncio.CancelledError:
                pass

    except Exception as e:
        logger.error(f"Connection failed: {e}")
        raise

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass

import threading
import asyncio
import logging
import websockets
from OcppInterface import OcppInterface

LOGGER = logging.getLogger('ocpp_worker')

class OcppWorker(threading.Thread):
    def __init__(self, csms_url, cp_id, charger):
        super().__init__()
        self.csms_url = csms_url
        self.cp_id = cp_id
        self.charger = charger
        self.daemon = True
        self.loop = None
        self.cp = None

    def run(self):
        LOGGER.info(f"Starting OCPP Worker for {self.cp_id} connecting to {self.csms_url}")
        try:
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            self.loop.run_until_complete(self._main())
        except Exception as e:
            LOGGER.error(f"OCPP Worker thread failed: {e}")

    async def _main(self):
        async with websockets.connect(
            self.csms_url,
            subprotocols=['ocpp2.0.1']
        ) as ws:
            self.cp = OcppInterface(self.cp_id, ws, self.charger)

            await asyncio.gather(
                self.cp.start(),
                self.cp.send_boot_notification(),
                self._heartbeat_loop()
            )

    async def _heartbeat_loop(self):
        while True:
            await asyncio.sleep(60) # Heartbeat interval
            if self.cp:
                await self.cp.send_heartbeat()

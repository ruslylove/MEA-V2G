import threading
import asyncio
import logging
import websockets
from Ocpp201Interface import Ocpp201Interface
from Ocpp16Interface import Ocpp16Interface
from Ocpp21Interface import Ocpp21Interface

LOGGER = logging.getLogger('ocpp_worker')

class OcppWorker(threading.Thread):
    def __init__(self, csms_url, cp_id, charger, ocpp_version='2.0.1'):
        super().__init__()
        self.csms_url = csms_url
        self.cp_id = cp_id
        self.charger = charger
        self.ocpp_version = ocpp_version
        self.daemon = True
        self.loop = None
        self.cp = None

    def run(self):
        LOGGER.info(f"Starting OCPP Worker for {self.cp_id} connecting to {self.csms_url} with version {self.ocpp_version}")
        try:
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            self.loop.run_until_complete(self._main())
        except Exception as e:
            LOGGER.error(f"OCPP Worker thread failed: {e}")

    async def _main(self):
        subprotocol = 'ocpp2.0.1'
        if self.ocpp_version == '1.6':
            subprotocol = 'ocpp1.6'
        elif self.ocpp_version == '2.1':
            subprotocol = 'ocpp2.1' # Note: Check server support. Often falls back or uses specific string.
        
        async with websockets.connect(
            self.csms_url,
            subprotocols=[subprotocol]
        ) as ws:
            if self.ocpp_version == '1.6':
                self.cp = Ocpp16Interface(self.cp_id, ws, self.charger)
            elif self.ocpp_version == '2.1':
                self.cp = Ocpp21Interface(self.cp_id, ws, self.charger)
            else:
                self.cp = Ocpp201Interface(self.cp_id, ws, self.charger)

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

#!/usr/bin/env python3
"""
WebSocket proxy: vSECC ws://local:9000 → MEA CSMS wss://...
Intercepts CALLERROR responses for unsupported OCPP extensions (SecurityEventNotification)
and converts them to empty success responses so the vSECC stays connected.
"""
import asyncio
import websockets
import ssl
import logging
import json

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("proxy")

MEA_URL = "wss://ocpp.measandbox.com:2930/EV/Srv/JSON/1.6/rddQC4000001"

# OCPP actions that MEA CSMS doesn't implement but we should silently ACK
SPOOF_OK_ACTIONS = {"SecurityEventNotification", "LogStatusNotification",
                    "SignCertificate", "CertificateSigned", "ExtendedTriggerMessage"}

ssl_ctx = ssl.create_default_context()
ssl_ctx.check_hostname = False
ssl_ctx.verify_mode = ssl.CERT_NONE


async def proxy(local_ws):
    try:
        path = local_ws.request.path
    except Exception:
        path = "?"
    log.info(f"vSECC connected, path={path}")

    # Track pending CALL msg_id -> action name (for spoofing CALLERROR → empty ACK)
    pending_calls = {}

    try:
        async with websockets.connect(MEA_URL, subprotocols=["ocpp1.6"],
                                      ssl=ssl_ctx, ping_interval=None) as remote_ws:
            log.info("Connected upstream to MEA CSMS")

            async def vsecc_to_mea():
                async for msg in local_ws:
                    try:
                        data = json.loads(msg)
                        if data[0] == 2:  # CALL
                            pending_calls[data[1]] = data[2]  # msg_id -> action
                        log.info(f"[vSECC→MEA] {str(msg)[:200]}")
                    except Exception:
                        pass
                    await remote_ws.send(msg)

            async def mea_to_vsecc():
                async for msg in remote_ws:
                    try:
                        data = json.loads(msg)
                        msg_type = data[0]
                        msg_id = data[1]
                        # CALLERROR (type 4) — check if we should spoof success
                        if msg_type == 4:
                            action = pending_calls.get(msg_id, "")
                            error_code = data[2] if len(data) > 2 else ""
                            if action in SPOOF_OK_ACTIONS or error_code == "NotImplemented":
                                spoof = json.dumps([3, msg_id, {}])
                                log.info(f"[SPOOF] Converting CALLERROR→ACK for {action} ({msg_id})")
                                await local_ws.send(spoof)
                                pending_calls.pop(msg_id, None)
                                continue
                        pending_calls.pop(msg_id, None)
                        log.info(f"[MEA→vSECC] {str(msg)[:200]}")
                    except Exception:
                        pass
                    await local_ws.send(msg)

            await asyncio.gather(vsecc_to_mea(), mea_to_vsecc())

    except Exception as e:
        log.error(f"Proxy error: {e}")
    log.info("Session ended")


async def main():
    async with websockets.serve(proxy, "0.0.0.0", 9000, subprotocols=["ocpp1.6"]):
        log.info("Proxy listening ws://0.0.0.0:9000  →  wss://MEA CSMS")
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())

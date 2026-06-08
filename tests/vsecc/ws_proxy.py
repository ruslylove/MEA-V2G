#!/usr/bin/env python3
"""
WebSocket proxy: vSECC ws://local:9000 → MEA CSMS wss://...

Port 9000 — WebSocket: vSECC OCPP client connects here; frames forwarded
             transparently to MEA CSMS.  Unsupported extension messages
             (SecurityEventNotification, etc.) are spoofed with an empty
             ACK so the session stays alive.

Port 9001 — HTTP control API (test harness injects OCPP CALLs to vSECC):
               POST /csms/send
               Body: {"action": "TriggerMessage", "payload": {...}}
               Returns: {"status": "ok", "response": {...}}  /  {"error": "..."}
               GET  /status → {"connected": true/false}
"""
import asyncio
import json
import logging
import ssl
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

import websockets

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("proxy")

MEA_URL = "wss://ocpp.measandbox.com:2930/EV/Srv/JSON/1.6/rddQC4000001"
CTRL_PORT = 9001

SPOOF_OK_ACTIONS = {
    "SecurityEventNotification", "LogStatusNotification",
    "SignCertificate", "CertificateSigned", "ExtendedTriggerMessage",
}

ssl_ctx = ssl.create_default_context()
ssl_ctx.check_hostname = False
ssl_ctx.verify_mode = ssl.CERT_NONE

# ─── Shared state ──────────────────────────────────────────────────────────
_loop: asyncio.AbstractEventLoop = None
_vsecc_ws = None                 # current vSECC WebSocket (local side)
_ctrl_pending: dict = {}         # msg_id → asyncio.Future (set in loop thread)


# ─── Coroutine submitted from HTTP thread → asyncio event loop ────────────
async def _ctrl_send_and_wait(call: str, msg_id: str) -> list:
    """Send a CALL to the vSECC and await its response.
    Called via run_coroutine_threadsafe from the HTTP handler thread.
    Returns the full OCPP response list, or raises on timeout/error.
    """
    if _vsecc_ws is None:
        raise RuntimeError("No vSECC connected")

    fut = _loop.create_future()
    _ctrl_pending[msg_id] = fut
    try:
        await _vsecc_ws.send(call)
        log.info(f"[CTRL→vSECC] {call[:300]}")
        result = await asyncio.wait_for(asyncio.shield(fut), timeout=20)
        return result
    except asyncio.TimeoutError:
        raise TimeoutError("Timeout waiting for vSECC response")
    finally:
        _ctrl_pending.pop(msg_id, None)


# ─── Proxy WebSocket handler ────────────────────────────────────────────────
async def proxy(local_ws):
    global _vsecc_ws
    try:
        path = local_ws.request.path
    except Exception:
        path = "?"
    log.info(f"vSECC connected, path={path}")
    _vsecc_ws = local_ws

    pending_calls = {}   # msg_id → action for vSECC-originated CALLs

    try:
        async with websockets.connect(
            MEA_URL, subprotocols=["ocpp1.6"],
            ssl=ssl_ctx, ping_interval=None
        ) as remote_ws:
            log.info("Connected upstream to MEA CSMS")

            async def vsecc_to_mea():
                async for msg in local_ws:
                    try:
                        data = json.loads(msg)
                        if data[0] == 2:
                            pending_calls[data[1]] = data[2]
                        elif data[0] in (3, 4) and data[1] in _ctrl_pending:
                            # Response to a ctrl-injected CALL — resolve and
                            # do NOT forward to MEA (it never sent the CALL)
                            fut = _ctrl_pending.pop(data[1])
                            if not fut.done():
                                fut.set_result(data)
                            log.info(f"[CTRL←vSECC] {str(msg)[:500]}")
                            continue
                        log.info(f"[vSECC→MEA] {str(msg)[:2000]}")
                    except Exception:
                        pass
                    await remote_ws.send(msg)

            async def mea_to_vsecc():
                async for msg in remote_ws:
                    try:
                        data = json.loads(msg)
                        msg_type, msg_id = data[0], data[1]
                        if msg_type == 4:
                            action = pending_calls.get(msg_id, "")
                            err_code = data[2] if len(data) > 2 else ""
                            if action in SPOOF_OK_ACTIONS or err_code == "NotImplemented":
                                spoof = json.dumps([3, msg_id, {}])
                                log.info(f"[SPOOF] CALLERROR→ACK for {action}")
                                await local_ws.send(spoof)
                                pending_calls.pop(msg_id, None)
                                continue
                        pending_calls.pop(msg_id, None)
                        log.info(f"[MEA→vSECC] {str(msg)[:2000]}")
                    except Exception:
                        pass
                    await local_ws.send(msg)

            await asyncio.gather(vsecc_to_mea(), mea_to_vsecc())

    except Exception as e:
        log.error(f"Proxy error: {e}")

    _vsecc_ws = None
    log.info("Session ended")


# ─── HTTP control server (runs in a daemon Thread) ─────────────────────────
class CtrlHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path == "/status":
            self._reply(200, {"connected": _vsecc_ws is not None})
        else:
            self._reply(404, {"error": "Not Found"})

    def do_POST(self):
        if self.path != "/csms/send":
            self._reply(404, {"error": "Not Found"})
            return
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        try:
            req = json.loads(body)
            action = req["action"]
            payload = req.get("payload", {})
        except Exception as e:
            self._reply(400, {"error": f"Bad request: {e}"})
            return

        if _vsecc_ws is None:
            self._reply(503, {"error": "No vSECC connected"})
            return

        msg_id = str(uuid.uuid4())
        call = json.dumps([2, msg_id, action, payload])

        # Submit coroutine to asyncio event loop; this returns a
        # concurrent.futures.Future that we can block on from this thread.
        cfut = asyncio.run_coroutine_threadsafe(
            _ctrl_send_and_wait(call, msg_id), _loop
        )
        try:
            result = cfut.result(timeout=25)
            if result[0] == 4:
                err_code = result[2] if len(result) > 2 else "Error"
                err_desc = result[3] if len(result) > 3 else ""
                self._reply(422, {"status": "error", "error": err_code,
                                  "description": err_desc, "response": {}})
            else:
                resp_payload = result[2] if len(result) > 2 else {}
                self._reply(200, {"status": "ok", "response": resp_payload})
        except TimeoutError as e:
            self._reply(504, {"error": str(e)})
        except Exception as e:
            self._reply(500, {"error": str(e)})

    def _reply(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def start_ctrl_server():
    srv = HTTPServer(("127.0.0.1", CTRL_PORT), CtrlHandler)
    t = Thread(target=srv.serve_forever, daemon=True)
    t.start()
    log.info(f"Control API listening on http://127.0.0.1:{CTRL_PORT}/csms/send")


# ─── Entry point ────────────────────────────────────────────────────────────
async def main():
    global _loop
    _loop = asyncio.get_running_loop()
    start_ctrl_server()
    async with websockets.serve(proxy, "0.0.0.0", 9000, subprotocols=["ocpp1.6"]):
        log.info("Proxy listening ws://0.0.0.0:9000  →  wss://MEA CSMS")
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())

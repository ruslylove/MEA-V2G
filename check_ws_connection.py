import asyncio
import websockets
import ssl

CHARGEPOINT_ID = "rddQC4000001"
DOMAINS = ["ocppapi.measandbox.com", "ocpp.measandbox.com"]
PATHS = [f"/ocpp/{CHARGEPOINT_ID}", f"/{CHARGEPOINT_ID}", f"/ocpp/1.6/{CHARGEPOINT_ID}"]
PROTOCOLS = ["wss", "ws"]

async def check_url(url):
    print(f"Checking {url}...")
    try:
        # Create SSL context that ignores cert errors (sandbox often uses self-signed)
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        
        async with websockets.connect(
            url, 
            subprotocols=['ocpp1.6'], 
            ssl=ssl_context if url.startswith('wss') else None,
            open_timeout=5
        ) as ws:
            print(f"SUCCESS: Connected to {url}")
            return True
    except Exception as e:
        print(f"FAILED: {url} - {e}")
        return False

async def main():
    for protocol in PROTOCOLS:
        for domain in DOMAINS:
            for path in PATHS:
                url = f"{protocol}://{domain}{path}"
                if await check_url(url):
                    print(f"Found valid URL: {url}")
                    return

if __name__ == "__main__":
    asyncio.run(main())

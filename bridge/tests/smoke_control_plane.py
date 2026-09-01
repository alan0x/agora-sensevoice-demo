"""Manual live smoke test for a running control plane and mock bridge."""

import asyncio
import json
import os
from urllib.parse import urlparse

import httpx
import websockets


async def main() -> None:
    base_url = os.getenv("SMOKE_BASE_URL", "http://127.0.0.1:18080")
    async with httpx.AsyncClient(base_url=base_url, timeout=5) as client:
        status = (await client.get("/api/v1/status")).raise_for_status().json()
        assert status["bridgeOnline"] is True, status
        session = (await client.post("/api/v1/sessions", json={})).raise_for_status().json()
        parsed = urlparse(base_url)
        ws_scheme = "wss" if parsed.scheme == "https" else "ws"
        events_url = f"{ws_scheme}://{parsed.netloc}{session['eventsWsPath']}"
        try:
            async with websockets.connect(events_url) as websocket:
                while True:
                    event = json.loads(await asyncio.wait_for(websocket.recv(), 5))
                    print(event)
                    if event.get("type") == "asr.final":
                        assert event.get("text")
                        break
        finally:
            await client.delete("/api/v1/sessions/" + session["sessionId"])


if __name__ == "__main__":
    asyncio.run(main())

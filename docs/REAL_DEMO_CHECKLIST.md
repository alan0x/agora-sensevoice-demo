# Real Agora + SenseVoice checklist

## Agora console

1. Create or select an RTC project and copy its App ID and App Certificate.
2. Keep the demo channel fixed as `sensevoice-demo`.
3. Generate two temporary RTC tokens for that same channel:
   - Browser publisher UID: `1001`.
   - LAN bridge subscriber UID: `9001`.
4. Give both tokens enough validity for the meeting, then replace them afterward.

The static token setup is suitable only for a short controlled demo. Do not put
the App Certificate in this repository, the browser, or the LAN bridge.

## VPS

1. Set `DEMO_MODE=false` in `deploy/.env`.
2. Fill `AGORA_APP_ID`, both UIDs and both RTC tokens. Set `PUBLIC_BASE_URL`
   and `ALLOWED_ORIGIN` to the exact HTTPS subdomain.
3. Do not start the `mock-bridge` Compose profile.
4. Proxy the HTTPS subdomain to `127.0.0.1:18080`; WebSocket upgrade headers are
   required. A sample is in `deploy/nginx.conf.example`.

## LAN bridge host

Use Linux x86_64 or arm64 with Python 3.10+:

```bash
cd bridge
python3.10 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt

export CONTROL_WS_URL=wss://asr-demo.example.com/ws/bridge
export BRIDGE_SHARED_SECRET='<same random value as VPS>'
export SENSEVOICE_URL='http://127.0.0.1:8000/v1/audio/transcriptions'
export SENSEVOICE_MODEL='SenseVoiceSmall'
python -m bridge.main --mode real
```

If SenseVoice does not expose an OpenAI-compatible multipart endpoint, adapt
`bridge/sensevoice.py`; no other component needs to change.

## Acceptance sequence

1. Open the HTTPS page and confirm “Bridge 在线 / REAL 真实链路”.
2. Click “开始识别” and approve microphone access.
3. Confirm Agora shows connected and the LAN bridge logs a remote UID.
4. Speak one short Chinese sentence and wait for the final transcript.
5. Try “立即断句”, mute/unmute, then end the session.
6. Confirm the browser never connects directly to the LAN address.

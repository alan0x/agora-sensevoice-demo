# Real Agora + OminiX ASR checklist

> Archived demo checklist. It applies to Git tag `demo-approved-2026-09-01` only.
> The current production branch uses dynamic AccessToken2 credentials; follow
> `docs/PRODUCTION_RUNBOOK.md` for deployment.

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

The current Apple Silicon Mac has already loaded Agora Server SDK 2.4.9 and has
a ready Python 3.13 virtual environment. For this demo:

```bash
cd bridge
bash start-ominix-asr.sh
```

Keep that terminal running. In a second terminal:

```bash
cd bridge
cp .env.example .env
# Set CONTROL_WS_URL and BRIDGE_SHARED_SECRET in .env.
bash start-real.sh
```

Agora documents macOS as a coding/testing platform, which fits this demo. Move
the bridge to a supported Linux host before production use.

The adapter already matches the running OminiX JSON/base64 contract. Before
joining Agora, it can be checked with a real 16 kHz mono WAV file:

```bash
python smoke_sensevoice_live.py /path/to/16k-mono.wav
```

## Acceptance sequence

1. Open the HTTPS page and confirm “Bridge 在线 / REAL 真实链路”.
2. Click “开始识别” and approve microphone access.
3. Confirm Agora shows connected and the LAN bridge logs a remote UID.
4. Speak one short Chinese sentence and wait for the final transcript.
5. Try “立即断句”, mute/unmute, then end the session.
6. Confirm the browser never connects directly to the LAN address.
7. Confirm the displayed final text matches the sentence actually spoken; mock
   output is not accepted for this checklist.

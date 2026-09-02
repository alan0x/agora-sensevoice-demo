# Production control protocol

The control plane intentionally carries JSON control messages and ASR text only.
Audio never flows through the VPS.

## Browser API

- `GET /healthz`: process liveness.
- `GET /readyz`: readiness; returns `200` only while the LAN Bridge is connected.
- `GET /api/v1/status`: public availability and capacity without credential or session IDs.
- `POST /api/v1/sessions`: allocate the single ASR worker session.
- `POST /api/v1/sessions/{id}/commit`: force an utterance boundary.
- `DELETE /api/v1/sessions/{id}`: stop the session.
- `GET /ws/client/{id}`: browser text-event WebSocket, authenticated by a path-scoped HttpOnly cookie.

All mutating browser APIs require `Authorization: Bearer <CLIENT_ACCESS_TOKEN>`.
The session response includes the Agora App ID, a unique channel, numeric UID
and dynamically issued short-lived AccessToken2 credential. The App Certificate
never leaves the VPS. The WebSocket ticket is not included in JSON or URLs.

## Bridge WebSocket

The LAN bridge opens `GET /ws/bridge` with:

```text
Authorization: Bearer <BRIDGE_SHARED_SECRET>
```

Control-plane to bridge:

```json
{"type":"session.start","sessionId":"...","agora":{"appId":"...","channel":"asr-...","uid":9001,"token":"007..."}}
{"type":"utterance.commit","sessionId":"..."}
{"type":"session.stop","sessionId":"..."}
```

Bridge to control-plane:

```json
{"type":"session.ready","sessionId":"..."}
{"type":"asr.partial","sessionId":"...","seq":1,"text":"正在识别"}
{"type":"asr.final","sessionId":"...","seq":2,"text":"最终结果"}
{"type":"asr.error","sessionId":"...","message":"..."}
{"type":"session.closed","sessionId":"..."}
```

## Lifecycle and current capacity

- One active session because the current OminiX worker is single-slot.
- Sessions live in memory and expire automatically; a restart requires a new session.
- Bridge or browser event-socket disconnect releases the active session.
- Each session uses an independent channel and independent short-lived RTC tokens.
- ASR results return over the control-plane WebSocket, not Agora RTM.

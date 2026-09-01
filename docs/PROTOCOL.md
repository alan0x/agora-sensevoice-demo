# Demo protocol

The control plane intentionally carries JSON control messages and ASR text only.
Audio never flows through the VPS.

## Browser API

- `GET /api/v1/status`: bridge and demo status.
- `POST /api/v1/sessions`: allocate the single demo session.
- `POST /api/v1/sessions/{id}/commit`: force an utterance boundary.
- `DELETE /api/v1/sessions/{id}`: stop the session.
- `GET /ws/client/{id}?ticket=...`: browser text-event WebSocket.

The session response includes the Agora App ID, fixed channel, numeric UID and
temporary RTC token. These credentials are used directly by Agora Web SDK.

## Bridge WebSocket

The LAN bridge opens `GET /ws/bridge` with:

```text
Authorization: Bearer <BRIDGE_SHARED_SECRET>
```

Control-plane to bridge:

```json
{"type":"session.start","sessionId":"...","agora":{"appId":"...","channel":"sensevoice-demo","uid":9001,"token":"..."}}
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

## Current non-production constraints

- One active session.
- Sessions live in memory.
- Two pre-generated RTC tokens use one fixed channel and two fixed UIDs.
- ASR results return over the control-plane WebSocket, not Agora RTM.

These constraints affect scale and credential management only. The presentation
path still uses real browser audio, Agora RTC, and OminiX ASR inference. The
production follow-up should add dynamic AccessToken2 issuance, persistent session
state, per-user authorization, rate limits and multi-session routing.

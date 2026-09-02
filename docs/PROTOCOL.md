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
{"type":"asr.partial","sessionId":"...","utteranceId":"...:1","seq":1,"text":"正在识别","metrics":{}}
{"type":"asr.final","sessionId":"...","utteranceId":"...:1","seq":2,"text":"最终结果","metrics":{}}
{"type":"trace.update","sessionId":"...","utteranceId":"...:1","seq":2,"eventType":"asr.final","metrics":{"bridge":{"resultWebSocketSendMs":1.2}}}
{"type":"asr.error","sessionId":"...","message":"..."}
{"type":"session.closed","sessionId":"..."}
```

Browser to control-plane over the authenticated client WebSocket:

```json
{"type":"client.result_ack","sessionId":"...","utteranceId":"...:1","eventType":"asr.final","seq":2}
```

The control plane scopes ACKs to the authenticated session and returns a `trace.update` containing `vpsBrowserAckRttMs` and the explicitly labelled RTT/2 estimate. It also adds `metrics.vps.relayQueueMs` before forwarding Bridge events.

The metrics object uses process-local monotonic durations:

- `metrics.audio`: audio duration, speech span and boundary reason.
- `metrics.agora`: Server SDK receive transport delay, jitter buffer, loss and bitrate.
- `metrics.bridge`: endpointing, audio queue, request preparation, OminiX HTTP, response parsing and result send.
- `metrics.asr`: real-time factor and optional OminiX `Server-Timing` durations.
- `metrics.vps`: control-plane receive/enqueue timestamps and relay queue duration.
- `metrics.delivery`: browser ACK RTT and estimated VPS-to-browser one-way duration.
- `metrics.browser`: speech-start/final, speech-end/final, first partial and DOM update durations.
- `metrics.summary`: same-utterance end-to-end estimate derived by summing the available media, endpointing, ASR, result delivery and render stages. `speechEndToFinalMs` is diagnostic only.

Wall-clock timestamps are diagnostic only and MUST NOT be subtracted across hosts. Raw audio never enters this protocol.

## Lifecycle and current capacity

- One active session because the current OminiX worker is single-slot.
- Sessions live in memory and expire automatically; a restart requires a new session.
- Bridge or browser event-socket disconnect releases the active session.
- Each session uses an independent channel and independent short-lived RTC tokens.
- ASR results return over the control-plane WebSocket, not Agora RTM.

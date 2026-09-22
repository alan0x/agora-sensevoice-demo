"""Outbound LAN bridge process."""

import argparse
import asyncio
import json
import logging
import os
import random
import signal
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

import websockets

from .agora_receiver import AgoraReceiver
from .segmenter import PcmSegmenter, SegmentEvent, SegmenterConfig
from .sensevoice import SenseVoiceClient

logger = logging.getLogger(__name__)


def env_float(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


def parse_asr_urls(urls_value: str, fallback_url: str = "") -> List[str]:
    """Parse the comma-separated ASR_URLS pool, falling back to a single URL."""
    urls = [url.strip() for url in urls_value.split(",") if url.strip()]
    if not urls and fallback_url.strip():
        urls = [fallback_url.strip()]
    return urls


class RealSession:
    def __init__(
        self,
        session_id: str,
        agora: Dict[str, Any],
        asr_url: str,
        emit: Callable[[Dict[str, Any]], Awaitable[None]],
    ) -> None:
        self.session_id = session_id
        self.emit = emit
        self.queue: asyncio.Queue[Optional[Tuple[bytes, int]]] = asyncio.Queue(maxsize=500)
        self.segmenter = PcmSegmenter(
            SegmenterConfig(threshold_dbfs=env_float("VAD_THRESHOLD_DBFS", -38.0))
        )
        self.asr = SenseVoiceClient(
            url=asr_url,
            model=os.getenv("ASR_MODEL", os.getenv("SENSEVOICE_MODEL", "qwen3-asr")),
            api_key=os.getenv("ASR_API_KEY", os.getenv("SENSEVOICE_API_KEY", "")),
            language=os.getenv(
                "ASR_LANGUAGE", os.getenv("SENSEVOICE_LANGUAGE", "Chinese")
            ),
            protocol=os.getenv(
                "ASR_PROTOCOL", os.getenv("SENSEVOICE_PROTOCOL", "octos-json")
            ),
        )
        self.receiver = AgoraReceiver(
            appid=agora["appId"],
            channel=agora["channel"],
            token=agora["token"],
            uid=int(agora["uid"]),
            on_pcm=self._on_pcm,
            on_network_stats=self._on_network_stats,
        )
        self.worker: Optional[asyncio.Task] = None
        self.partial_task: Optional[asyncio.Task] = None
        self.sequence = 0
        self.network_stats: Dict[str, Any] = {}

    async def start(self) -> None:
        await self.receiver.start()
        self.worker = asyncio.create_task(self._process_audio())

    def _on_pcm(self, pcm: bytes, received_ns: int) -> None:
        try:
            self.queue.put_nowait((pcm, received_ns))
        except asyncio.QueueFull:
            logger.warning("audio queue full; dropping one PCM frame")

    def _on_network_stats(self, stats: Dict[str, Any]) -> None:
        self.network_stats = stats

    async def _process_audio(self) -> None:
        while True:
            item = await self.queue.get()
            if item is None:
                return
            pcm, received_ns = item
            for event in self.segmenter.feed(pcm, received_ns):
                await self._handle_segment(event)

    async def _handle_segment(self, event: SegmentEvent) -> None:
        if event.kind == "partial":
            if self.partial_task is None or self.partial_task.done():
                self.partial_task = asyncio.create_task(
                    self._transcribe(event, final=False)
                )
            return
        if self.partial_task and not self.partial_task.done():
            self.partial_task.cancel()
        await self._transcribe(event, final=True)

    async def _transcribe(self, event: SegmentEvent, final: bool) -> None:
        utterance_id = f"{self.session_id}:{event.utterance_index}"
        asr_started_ns = time.perf_counter_ns()
        try:
            result = await self.asr.transcribe_detailed(event.pcm)
            if not result.text:
                return
            self.sequence += 1
            speech_span_ms = max(
                0.0, (event.last_voice_ns - event.speech_started_ns) / 1_000_000
            )
            audio_duration_ms = max(result.audio_duration_ms, 0.001)
            metrics = {
                "schemaVersion": 1,
                "audio": {
                    "durationMs": round(result.audio_duration_ms, 2),
                    "speechSpanMs": round(speech_span_ms, 2),
                    "boundaryReason": event.boundary_reason,
                },
                "agora": dict(self.network_stats),
                "bridge": {
                    "endpointMs": round(event.endpoint_ms, 2),
                    "audioQueueMs": round(
                        max(0.0, (asr_started_ns - event.emitted_ns) / 1_000_000), 2
                    ),
                    "asrRequestPrepareMs": round(result.request_prepare_ms, 2),
                    "asrHttpRoundTripMs": round(result.http_round_trip_ms, 2),
                    "asrResponseParseMs": round(result.response_parse_ms, 2),
                    "asrTotalMs": round(result.total_ms, 2),
                },
                "asr": {
                    "rtf": round(result.total_ms / audio_duration_ms, 4),
                    "serverTimingMs": result.server_timing_ms,
                },
            }
            payload = {
                "type": "asr.final" if final else "asr.partial",
                "sessionId": self.session_id,
                "utteranceId": utterance_id,
                "seq": self.sequence,
                "text": result.text,
                "metrics": metrics,
                "bridgeResultReadyAtUnixMs": int(time.time() * 1000),
            }
            send_started_ns = time.perf_counter_ns()
            await self.emit(payload)
            send_ms = (time.perf_counter_ns() - send_started_ns) / 1_000_000
            await self.emit(
                {
                    "type": "trace.update",
                    "sessionId": self.session_id,
                    "utteranceId": utterance_id,
                    "seq": self.sequence,
                    "eventType": payload["type"],
                    "metrics": {
                        "bridge": {
                            "resultWebSocketSendMs": round(send_ms, 2),
                        }
                    },
                }
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("ASR request failed")
            await self.emit(
                {
                    "type": "asr.error",
                    "sessionId": self.session_id,
                    "utteranceId": utterance_id,
                    "message": str(exc),
                }
            )

    async def commit(self) -> None:
        for event in self.segmenter.commit():
            await self._handle_segment(event)

    async def stop(self) -> None:
        self.receiver.stop()
        if self.worker:
            await self.queue.put(None)
            await self.worker
        if self.partial_task and not self.partial_task.done():
            self.partial_task.cancel()
        await self.asr.close()


class MockSession:
    LINES = [
        "声网负责把浏览器的实时音频安全地送到内网桥接器。",
        "OminiX ASR 仍然运行在公司内网，不需要开放入站端口。",
        "这台 VPS 只负责会话控制和识别文本转发。",
    ]

    def __init__(
        self,
        session_id: str,
        emit: Callable[[Dict[str, Any]], Awaitable[None]],
    ) -> None:
        self.session_id = session_id
        self.emit = emit
        self.task: Optional[asyncio.Task] = None
        self.index = 0
        self.sequence = 0

    async def start(self) -> None:
        self.task = asyncio.create_task(self._play())

    async def _play(self) -> None:
        for line in self.LINES:
            utterance_id = f"{self.session_id}:mock-{self.index + 1}"
            await asyncio.sleep(0.7)
            self.sequence += 1
            await self.emit(
                {
                    "type": "asr.partial",
                    "sessionId": self.session_id,
                    "utteranceId": utterance_id,
                    "seq": self.sequence,
                    "text": line[: max(4, len(line) // 2)],
                }
            )
            await asyncio.sleep(0.65)
            self.sequence += 1
            await self.emit(
                {
                    "type": "asr.final",
                    "sessionId": self.session_id,
                    "utteranceId": utterance_id,
                    "seq": self.sequence,
                    "text": line,
                    "metrics": {
                        "schemaVersion": 1,
                        "audio": {"durationMs": 2400, "boundaryReason": "mock"},
                        "agora": {
                            "networkTransportDelayMs": 48,
                            "jitterBufferDelayMs": 22,
                            "audioLossRatePercent": 0,
                        },
                        "bridge": {
                            "endpointMs": 650,
                            "asrRequestPrepareMs": 3,
                            "asrHttpRoundTripMs": 420,
                            "asrResponseParseMs": 1,
                            "asrTotalMs": 424,
                        },
                        "asr": {"rtf": 0.1767},
                        "mock": True,
                    },
                }
            )
            self.index += 1

    async def commit(self) -> None:
        self.index += 1
        self.sequence += 1
        await self.emit(
            {
                "type": "asr.final",
                "sessionId": self.session_id,
                "utteranceId": f"{self.session_id}:mock-{self.index}",
                "seq": self.sequence,
                "text": "手动断句成功：端到端控制链路工作正常。",
            }
        )

    async def stop(self) -> None:
        if self.task and not self.task.done():
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass


class BridgeApp:
    def __init__(
        self,
        mode: str,
        ws_url: str,
        shared_secret: str,
        asr_urls: Optional[List[str]] = None,
        max_sessions: int = 64,
    ) -> None:
        self.mode = mode
        self.ws_url = ws_url
        self.shared_secret = shared_secret
        self.asr_urls = asr_urls or []
        self.max_sessions = max_sessions
        self.websocket: Any = None
        self.send_lock = asyncio.Lock()
        self.sessions: Dict[str, Any] = {}
        self._asr_rr_index = 0
        self.stopping = asyncio.Event()

    def _next_asr_url(self) -> str:
        url = self.asr_urls[self._asr_rr_index % len(self.asr_urls)]
        self._asr_rr_index += 1
        return url

    async def emit(self, payload: Dict[str, Any]) -> None:
        async with self.send_lock:
            if self.websocket is not None:
                await self.websocket.send(json.dumps(payload, ensure_ascii=False))

    async def handle(self, payload: Dict[str, Any]) -> None:
        event_type = payload.get("type")
        session_id = payload.get("sessionId")
        if event_type == "session.start":
            if session_id in self.sessions:
                await self.stop_session(session_id, notify=False)
            if len(self.sessions) >= self.max_sessions:
                logger.warning("bridge at capacity; rejecting session %s", session_id)
                await self.emit(
                    {
                        "type": "asr.error",
                        "sessionId": session_id,
                        "message": "Bridge is at its session capacity",
                    }
                )
                return
            if self.mode == "mock":
                session = MockSession(session_id, self.emit)
            else:
                if not self.asr_urls:
                    raise RuntimeError("ASR_URLS or ASR_URL is required in real mode")
                session = RealSession(
                    session_id, payload["agora"], self._next_asr_url(), self.emit
                )
            self.sessions[session_id] = session
            try:
                await session.start()
                await self.emit({"type": "session.ready", "sessionId": session_id})
                logger.info(
                    "session ready: %s (active=%d)", session_id, len(self.sessions)
                )
            except Exception as exc:
                logger.exception("session startup failed")
                await self.emit(
                    {
                        "type": "asr.error",
                        "sessionId": session_id,
                        "message": str(exc),
                    }
                )
                await self.stop_session(session_id, notify=False)
        elif event_type == "utterance.commit":
            session = self.sessions.get(session_id)
            if session is not None:
                await session.commit()
        elif event_type == "session.stop":
            await self.stop_session(session_id, notify=True)

    async def stop_session(self, session_id: Optional[str], notify: bool) -> None:
        session = self.sessions.pop(session_id, None)
        if session is None:
            return
        try:
            await session.stop()
        finally:
            if notify:
                await self.emit({"type": "session.closed", "sessionId": session_id})

    async def stop_all_sessions(self) -> None:
        for session_id in list(self.sessions):
            await self.stop_session(session_id, notify=False)

    async def run(self) -> None:
        backoff = 1.0
        while not self.stopping.is_set():
            try:
                logger.info("connecting control plane: %s", self.ws_url)
                async with websockets.connect(
                    self.ws_url,
                    additional_headers={
                        "Authorization": "Bearer " + self.shared_secret
                    },
                    # The control plane is directly reachable over public HTTPS.
                    # Ignore shell/system SOCKS proxy variables; websockets 15
                    # otherwise requires the optional python-socks dependency.
                    proxy=None,
                    ping_interval=20,
                    ping_timeout=20,
                    max_size=1_048_576,
                ) as websocket:
                    self.websocket = websocket
                    backoff = 1.0
                    logger.info("bridge connected in %s mode", self.mode)
                    async for message in websocket:
                        try:
                            await self.handle(json.loads(message))
                        except Exception:
                            logger.exception("failed to handle control message")
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("bridge connection lost: %s", exc)
            finally:
                self.websocket = None
                await self.stop_all_sessions()
            if not self.stopping.is_set():
                await asyncio.sleep(backoff + random.random() * 0.25)
                backoff = min(backoff * 2, 20.0)

    async def stop(self) -> None:
        self.stopping.set()
        await self.stop_all_sessions()
        if self.websocket is not None:
            await self.websocket.close()
        AgoraReceiver.shutdown_service()


async def async_main(args: argparse.Namespace) -> None:
    secret = os.getenv("BRIDGE_SHARED_SECRET", "")
    if len(secret) < 16:
        raise SystemExit("BRIDGE_SHARED_SECRET must contain at least 16 characters")
    asr_urls = parse_asr_urls(
        os.getenv("ASR_URLS", ""),
        os.getenv("ASR_URL") or os.getenv("SENSEVOICE_URL", ""),
    )
    if args.mode == "real" and not asr_urls:
        raise SystemExit("ASR_URLS or ASR_URL is required in real mode")
    max_sessions = int(os.getenv("BRIDGE_MAX_SESSIONS", "64"))
    app = BridgeApp(args.mode, args.control_ws_url, secret, asr_urls, max_sessions)
    loop = asyncio.get_running_loop()
    for signal_name in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signal_name, lambda: asyncio.create_task(app.stop()))
    try:
        await app.run()
    finally:
        await app.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description="Agora to private ASR LAN bridge")
    parser.add_argument(
        "--mode", choices=("mock", "real"), default=os.getenv("BRIDGE_MODE", "real")
    )
    parser.add_argument(
        "--control-ws-url",
        default=os.getenv("CONTROL_WS_URL", "ws://localhost:8080/ws/bridge"),
    )
    parser.add_argument("--log-level", default=os.getenv("LOG_LEVEL", "INFO"))
    args = parser.parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    asyncio.run(async_main(args))


if __name__ == "__main__":
    main()

"""Outbound LAN bridge process."""

import argparse
import asyncio
import json
import logging
import os
import random
import signal
from typing import Any, Awaitable, Callable, Dict, Optional

import websockets

from .agora_receiver import AgoraReceiver
from .segmenter import PcmSegmenter, SegmentEvent, SegmenterConfig
from .sensevoice import SenseVoiceClient

logger = logging.getLogger(__name__)


def env_float(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


class RealSession:
    def __init__(
        self,
        session_id: str,
        agora: Dict[str, Any],
        emit: Callable[[Dict[str, Any]], Awaitable[None]],
    ) -> None:
        self.session_id = session_id
        self.emit = emit
        self.queue: asyncio.Queue[Optional[bytes]] = asyncio.Queue(maxsize=500)
        self.segmenter = PcmSegmenter(
            SegmenterConfig(threshold_dbfs=env_float("VAD_THRESHOLD_DBFS", -38.0))
        )
        self.asr = SenseVoiceClient(
            url=os.environ["SENSEVOICE_URL"],
            model=os.getenv("SENSEVOICE_MODEL", "SenseVoiceSmall"),
            api_key=os.getenv("SENSEVOICE_API_KEY", ""),
            language=os.getenv("SENSEVOICE_LANGUAGE", "auto"),
        )
        self.receiver = AgoraReceiver(
            appid=agora["appId"],
            channel=agora["channel"],
            token=agora["token"],
            uid=int(agora["uid"]),
            on_pcm=self._on_pcm,
        )
        self.worker: Optional[asyncio.Task] = None
        self.partial_task: Optional[asyncio.Task] = None
        self.sequence = 0

    async def start(self) -> None:
        await self.receiver.start()
        self.worker = asyncio.create_task(self._process_audio())

    def _on_pcm(self, pcm: bytes) -> None:
        try:
            self.queue.put_nowait(pcm)
        except asyncio.QueueFull:
            logger.warning("audio queue full; dropping one PCM frame")

    async def _process_audio(self) -> None:
        while True:
            pcm = await self.queue.get()
            if pcm is None:
                return
            for event in self.segmenter.feed(pcm):
                await self._handle_segment(event)

    async def _handle_segment(self, event: SegmentEvent) -> None:
        if event.kind == "partial":
            if self.partial_task is None or self.partial_task.done():
                self.partial_task = asyncio.create_task(
                    self._transcribe(event.pcm, final=False)
                )
            return
        if self.partial_task and not self.partial_task.done():
            self.partial_task.cancel()
        await self._transcribe(event.pcm, final=True)

    async def _transcribe(self, pcm: bytes, final: bool) -> None:
        try:
            text = await self.asr.transcribe(pcm)
            if not text:
                return
            self.sequence += 1
            await self.emit(
                {
                    "type": "asr.final" if final else "asr.partial",
                    "sessionId": self.session_id,
                    "seq": self.sequence,
                    "text": text,
                }
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("SenseVoice request failed")
            await self.emit(
                {
                    "type": "asr.error",
                    "sessionId": self.session_id,
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
        "SenseVoice 仍然运行在公司内网，不需要开放入站端口。",
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

    async def start(self) -> None:
        self.task = asyncio.create_task(self._play())

    async def _play(self) -> None:
        for line in self.LINES:
            await asyncio.sleep(0.7)
            await self.emit(
                {
                    "type": "asr.partial",
                    "sessionId": self.session_id,
                    "text": line[: max(4, len(line) // 2)],
                }
            )
            await asyncio.sleep(0.65)
            await self.emit(
                {"type": "asr.final", "sessionId": self.session_id, "text": line}
            )
            self.index += 1

    async def commit(self) -> None:
        await self.emit(
            {
                "type": "asr.final",
                "sessionId": self.session_id,
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
    def __init__(self, mode: str, ws_url: str, shared_secret: str) -> None:
        self.mode = mode
        self.ws_url = ws_url
        self.shared_secret = shared_secret
        self.websocket: Any = None
        self.send_lock = asyncio.Lock()
        self.session: Optional[Any] = None
        self.session_id: Optional[str] = None
        self.stopping = asyncio.Event()

    async def emit(self, payload: Dict[str, Any]) -> None:
        async with self.send_lock:
            if self.websocket is not None:
                await self.websocket.send(json.dumps(payload, ensure_ascii=False))

    async def handle(self, payload: Dict[str, Any]) -> None:
        event_type = payload.get("type")
        session_id = payload.get("sessionId")
        if event_type == "session.start":
            await self.stop_session(notify=False)
            self.session_id = session_id
            if self.mode == "mock":
                self.session = MockSession(session_id, self.emit)
            else:
                if not os.getenv("SENSEVOICE_URL"):
                    raise RuntimeError("SENSEVOICE_URL is required in real mode")
                self.session = RealSession(session_id, payload["agora"], self.emit)
            try:
                await self.session.start()
                await self.emit({"type": "session.ready", "sessionId": session_id})
                logger.info("session ready: %s", session_id)
            except Exception as exc:
                logger.exception("session startup failed")
                await self.emit(
                    {
                        "type": "asr.error",
                        "sessionId": session_id,
                        "message": str(exc),
                    }
                )
                await self.stop_session(notify=False)
        elif event_type == "utterance.commit" and session_id == self.session_id:
            await self.session.commit()
        elif event_type == "session.stop" and session_id == self.session_id:
            await self.stop_session(notify=True)

    async def stop_session(self, notify: bool) -> None:
        if self.session is None:
            return
        old_id = self.session_id
        try:
            await self.session.stop()
        finally:
            self.session = None
            self.session_id = None
        if notify and old_id:
            await self.emit({"type": "session.closed", "sessionId": old_id})

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
                await self.stop_session(notify=False)
            if not self.stopping.is_set():
                await asyncio.sleep(backoff + random.random() * 0.25)
                backoff = min(backoff * 2, 20.0)

    async def stop(self) -> None:
        self.stopping.set()
        await self.stop_session(notify=False)
        if self.websocket is not None:
            await self.websocket.close()
        AgoraReceiver.shutdown_service()


async def async_main(args: argparse.Namespace) -> None:
    secret = os.getenv("BRIDGE_SHARED_SECRET", "")
    if len(secret) < 16:
        raise SystemExit("BRIDGE_SHARED_SECRET must contain at least 16 characters")
    app = BridgeApp(args.mode, args.control_ws_url, secret)
    loop = asyncio.get_running_loop()
    for signal_name in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signal_name, lambda: asyncio.create_task(app.stop()))
    try:
        await app.run()
    finally:
        await app.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description="Agora to SenseVoice LAN bridge")
    parser.add_argument(
        "--mode", choices=("mock", "real"), default=os.getenv("BRIDGE_MODE", "mock")
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

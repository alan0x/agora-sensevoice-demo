"""LiveKit RTC transport for the bridge.

Mirrors the AgoraReceiver interface so sessions can run on either provider:
subscribe to the browser microphone as 16 kHz PCM, and publish synthesized
TTS audio back into the room. The `livekit` package is imported lazily in
start() so tests and Agora-only deployments do not need it installed.
"""

import asyncio
import logging
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

PUSH_FRAME_MS = 10
PUSH_QUEUE_MAX_FRAMES = 200  # 2 s of 10 ms frames


def slice_push_frames(pcm: bytes, sample_rate: int, channels: int) -> List[bytes]:
    """Split PCM into whole 10 ms frames, padding the tail with silence."""
    bytes_per_frame = sample_rate * channels * 2 * PUSH_FRAME_MS // 1000
    frames = []
    for offset in range(0, len(pcm), bytes_per_frame):
        chunk = pcm[offset : offset + bytes_per_frame]
        if len(chunk) < bytes_per_frame:
            chunk = chunk.ljust(bytes_per_frame, b"\x00")
        frames.append(bytes(chunk))
    return frames


class LivekitReceiver:
    def __init__(
        self,
        url: str,
        room: str,
        token: str,
        on_pcm: Callable[[bytes, int], None],
        on_network_stats: Optional[Callable[[dict], None]] = None,
    ) -> None:
        self.url = url
        self.room_name = room
        self.token = token
        self.on_pcm = on_pcm
        self.on_network_stats = on_network_stats
        self.room: Any = None
        self._rtc: Any = None
        self._audio_source: Any = None
        self._reader_task: Optional[asyncio.Task] = None
        self._sender_task: Optional[asyncio.Task] = None
        self._push_queue: "asyncio.Queue[Any]" = asyncio.Queue(
            maxsize=PUSH_QUEUE_MAX_FRAMES
        )

    async def start(self) -> None:
        try:
            from livekit import rtc
        except ImportError as exc:
            raise RuntimeError(
                "livekit SDK is missing; install it with 'pip install livekit'"
            ) from exc
        self._rtc = rtc

        room = rtc.Room()

        @room.on("track_subscribed")
        def _on_track_subscribed(track, publication, participant):  # noqa: ANN001
            if track.kind == rtc.TrackKind.KIND_AUDIO:
                self._reader_task = asyncio.create_task(self._read_track(track))

        await room.connect(self.url, self.token)
        self.room = room

        # TTS playback track: the browser subscribes to this for speak output.
        self._audio_source = rtc.AudioSource(48_000, 1)
        tts_track = rtc.LocalAudioTrack.create_audio_track("tts", self._audio_source)
        await room.local_participant.publish_track(tts_track)

        self._sender_task = asyncio.create_task(self._send_loop())
        logger.info("livekit connected: room=%s", self.room_name)

    async def _read_track(self, track: Any) -> None:
        rtc = self._rtc
        resampler = rtc.AudioResampler(input_rate=48_000, output_rate=16_000)
        stream = rtc.AudioStream(track)
        try:
            async for event in stream:
                for frame in resampler.push(event.frame):
                    pcm = bytes(frame.data)
                    if pcm:
                        self.on_pcm(pcm, time.perf_counter_ns())
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("livekit audio stream failed")

    async def _send_loop(self) -> None:
        while True:
            frame = await self._push_queue.get()
            if frame is None:
                return
            try:
                await self._audio_source.capture_frame(frame)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("livekit capture_frame failed")

    def push_pcm(self, pcm: bytes, sample_rate: int = 48_000, channels: int = 1) -> bool:
        """Queue PCM toward the room; same sync contract as AgoraReceiver."""
        if self.room is None or self._rtc is None or not pcm:
            return False
        rtc = self._rtc
        samples_per_frame = sample_rate * PUSH_FRAME_MS // 1000
        for chunk in slice_push_frames(pcm, sample_rate, channels):
            frame = rtc.AudioFrame(
                data=chunk,
                sample_rate=sample_rate,
                num_channels=channels,
                samples_per_channel=samples_per_frame,
            )
            try:
                self._push_queue.put_nowait(frame)
            except asyncio.QueueFull:
                # Stay realtime: drop the oldest queued frame and retry once.
                try:
                    self._push_queue.get_nowait()
                    self._push_queue.put_nowait(frame)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    return False
        return True

    def push_ready(self) -> bool:
        return not self._push_queue.full()

    def clear_audio_buffer(self) -> None:
        """Drop all queued but unsent frames (barge-in / session teardown)."""
        while True:
            try:
                self._push_queue.get_nowait()
            except asyncio.QueueEmpty:
                return

    async def stop(self) -> None:
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
        try:
            self._push_queue.put_nowait(None)
        except asyncio.QueueFull:
            pass
        if self._sender_task and not self._sender_task.done():
            self._sender_task.cancel()
            try:
                await self._sender_task
            except asyncio.CancelledError:
                pass
        if self.room is not None:
            try:
                await self.room.disconnect()
            except Exception:
                logger.exception("livekit disconnect failed")
            self.room = None

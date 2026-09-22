"""Streaming Qwen3-TTS client and PCM resampling helpers.

OminiX streams raw 16-bit mono PCM at 24 kHz from /v1/audio/speech. Agora does
not accept a 24 kHz PCM feed, so frames are upsampled 2x to 48 kHz before being
pushed into the RTC connection.
"""

from typing import AsyncIterator, Optional

import httpx

TTS_SOURCE_RATE = 24_000
TTS_TARGET_RATE = 48_000


class PcmUpsampler2x:
    """16-bit mono PCM 2x linear-interpolation upsampler.

    Keeps a one-sample carry so chunk boundaries stay continuous; an odd
    trailing byte is carried over to the next feed() call.
    """

    def __init__(self) -> None:
        self._pending = bytearray()
        self._last_sample: Optional[int] = None

    def feed(self, pcm: bytes) -> bytes:
        data = bytes(self._pending) + pcm
        usable = len(data) - (len(data) % 2)
        self._pending = bytearray(data[usable:])
        out = bytearray()
        previous = self._last_sample
        for offset in range(0, usable, 2):
            sample = int.from_bytes(data[offset : offset + 2], "little", signed=True)
            if previous is not None:
                midpoint = (previous + sample) // 2
                out += midpoint.to_bytes(2, "little", signed=True)
            out += sample.to_bytes(2, "little", signed=True)
            previous = sample
        self._last_sample = previous
        return bytes(out)


class TtsClient:
    def __init__(self, url: str, voice: str = "vivian", timeout_seconds: float = 120.0) -> None:
        self.url = url
        self.voice = voice
        self._client = httpx.AsyncClient(timeout=timeout_seconds)

    async def stream_pcm(self, text: str, voice: Optional[str] = None) -> AsyncIterator[bytes]:
        body = {
            "input": text,
            "model": "qwen3-tts",
            "response_format": "pcm",
        }
        selected_voice = (voice or "").strip() or self.voice
        if selected_voice:
            body["voice"] = selected_voice
        async with self._client.stream("POST", self.url, json=body) as response:
            response.raise_for_status()
            async for chunk in response.aiter_bytes():
                if chunk:
                    yield chunk

    async def close(self) -> None:
        await self._client.aclose()

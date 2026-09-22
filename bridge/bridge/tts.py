"""Streaming Qwen3-TTS client and PCM resampling helpers.

OminiX streams raw 16-bit mono PCM at 24 kHz from /v1/audio/speech. Agora does
not accept a 24 kHz PCM feed, so frames are upsampled 2x to 48 kHz before being
pushed into the RTC connection.
"""

from typing import AsyncIterator, Optional

import httpx

TTS_SOURCE_RATE = 24_000
TTS_TARGET_RATE = 48_000

# Half-width punctuation that confuses OminiX sentence splitting (it only
# splits CJK punctuation inside Chinese text) and the Qwen3-TTS tokenizer.
_CJK_PUNCT = str.maketrans({",": "，", ";": "；", "?": "？", "!": "！"})


def _has_cjk(text: str) -> bool:
    return any("一" <= char <= "鿿" for char in text)


def normalize_tts_text(text: str) -> str:
    """Convert half-width punctuation to full-width for Chinese text.

    OminiX splits sentences only on CJK punctuation, and the model handles
    native punctuation more robustly; leave pure-ASCII text untouched.
    """
    if _has_cjk(text):
        return text.translate(_CJK_PUNCT)
    return text


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
    def __init__(
        self,
        url: str,
        voice: str = "serena",
        speed: float = 1.0,
        instruct: str = "",
        language: str = "chinese",
        timeout_seconds: float = 120.0,
    ) -> None:
        self.url = url
        self.voice = voice
        self.speed = speed
        self.instruct = instruct
        self.language = language
        self._client = httpx.AsyncClient(timeout=timeout_seconds)

    async def stream_pcm(self, text: str, voice: Optional[str] = None) -> AsyncIterator[bytes]:
        body = {
            "input": normalize_tts_text(text),
            "model": "qwen3-tts",
            "response_format": "pcm",
            "language": self.language,
        }
        selected_voice = (voice or "").strip() or self.voice
        if selected_voice:
            body["voice"] = selected_voice
        if self.instruct:
            body["instruct"] = self.instruct
        if self.speed != 1.0:
            body["speed"] = self.speed
        async with self._client.stream("POST", self.url, json=body) as response:
            response.raise_for_status()
            async for chunk in response.aiter_bytes():
                if chunk:
                    yield chunk

    async def close(self) -> None:
        await self._client.aclose()

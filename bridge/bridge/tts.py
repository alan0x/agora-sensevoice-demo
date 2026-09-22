"""Streaming Qwen3-TTS client and PCM resampling helpers.

OminiX streams raw 16-bit mono PCM at 24 kHz from /v1/audio/speech. Agora does
not accept a 24 kHz PCM feed, so frames are upsampled 2x to 48 kHz before being
pushed into the RTC connection.
"""

from typing import AsyncIterator, Optional

import httpx

TTS_SOURCE_RATE = 24_000
TTS_TARGET_RATE = 48_000

# Half-width punctuation mapping
_CJK_TERMINATORS = str.maketrans({"?": "？", "!": "！"})
_CJK_COMMAS_TO_ASCII = str.maketrans({"，": ",", "；": ";", "、": ","})
_ASCII_TO_CJK_FULL = str.maketrans({",": "，", ";": "；", "?": "？", "!": "！"})


def _has_cjk(text: str) -> bool:
    return any("一" <= char <= "鿿" for char in text)


def normalize_tts_text(text: str, avoid_comma_split: bool = True) -> str:
    """Normalize punctuation for Qwen3-TTS.

    If avoid_comma_split is True (default), clause separators (，, ；, 、) are
    mapped to half-width so OminiX does not chop clauses into micro-fragments
    that suffer prosody discontinuities and duration stalling (extreme slow speech).
    Sentence terminators (？, ！) are kept as CJK punctuation.

    If avoid_comma_split is False, legacy normalization to full-width is used.
    """
    if not _has_cjk(text):
        return text

    if avoid_comma_split:
        text = text.translate(_CJK_TERMINATORS)
        return text.translate(_CJK_COMMAS_TO_ASCII)
    return text.translate(_ASCII_TO_CJK_FULL)


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
        temperature: float = 0.2,
        top_p: float = 0.8,
        seed: Optional[int] = None,
        avoid_comma_split: bool = True,
        timeout_seconds: float = 120.0,
    ) -> None:
        self.url = url
        self.voice = voice
        self.speed = speed
        self.instruct = instruct
        self.language = language
        self.temperature = temperature
        self.top_p = top_p
        self.seed = seed
        self.avoid_comma_split = avoid_comma_split
        self._client = httpx.AsyncClient(timeout=timeout_seconds)

    async def stream_pcm(self, text: str, voice: Optional[str] = None) -> AsyncIterator[bytes]:
        body = {
            "input": normalize_tts_text(text, avoid_comma_split=self.avoid_comma_split),
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
        if self.temperature is not None:
            body["temperature"] = self.temperature
        if self.top_p is not None:
            body["top_p"] = self.top_p
        if self.seed is not None:
            body["seed"] = self.seed
        async with self._client.stream("POST", self.url, json=body) as response:
            response.raise_for_status()
            async for chunk in response.aiter_bytes():
                if chunk:
                    yield chunk

    async def close(self) -> None:
        await self._client.aclose()


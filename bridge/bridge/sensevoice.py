"""SenseVoice HTTP adapter for the ASR server running on this Mac."""

import base64
import io
import wave
from typing import Any

import httpx


class SenseVoiceClient:
    def __init__(
        self,
        url: str,
        model: str = "SenseVoiceSmall",
        api_key: str = "",
        language: str = "Chinese",
        protocol: str = "octos-json",
        timeout_seconds: float = 30.0,
    ) -> None:
        self.url = url
        self.model = model
        self.api_key = api_key
        self.language = language
        self.protocol = protocol
        self._client = httpx.AsyncClient(timeout=timeout_seconds)

    async def transcribe(self, pcm: bytes) -> str:
        wav_bytes = pcm16_to_wav(pcm)
        headers = {}
        if self.api_key:
            headers["Authorization"] = "Bearer " + self.api_key
        if self.protocol == "octos-json":
            response = await self._client.post(
                self.url,
                headers=headers,
                json={
                    "file": base64.b64encode(wav_bytes).decode("ascii"),
                    "language": self.language,
                    "response_format": "verbose_json",
                },
            )
        elif self.protocol == "openai-multipart":
            data = {"model": self.model, "response_format": "json"}
            if self.language and self.language != "auto":
                data["language"] = self.language
            response = await self._client.post(
                self.url,
                headers=headers,
                data=data,
                files={"file": ("speech.wav", wav_bytes, "audio/wav")},
            )
        else:
            raise ValueError("Unsupported SENSEVOICE_PROTOCOL: " + self.protocol)
        response.raise_for_status()
        return extract_text(response.json()).strip()

    async def close(self) -> None:
        await self._client.aclose()


def pcm16_to_wav(pcm: bytes, sample_rate: int = 16_000) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm)
    return output.getvalue()


def extract_text(payload: Any) -> str:
    if isinstance(payload, str):
        return payload
    if isinstance(payload, list) and payload:
        return extract_text(payload[0])
    if not isinstance(payload, dict):
        raise ValueError("SenseVoice response does not contain text")
    for key in ("text", "transcript", "result"):
        value = payload.get(key)
        if isinstance(value, str):
            return value
    for key in ("data", "output"):
        value = payload.get(key)
        if value is not None:
            try:
                return extract_text(value)
            except ValueError:
                pass
    raise ValueError("SenseVoice response does not contain text")

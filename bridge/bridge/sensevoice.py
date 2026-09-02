"""JSON/base64 adapter for the private ASR server running on this Mac."""

import base64
from dataclasses import dataclass
import io
import time
import wave
from typing import Any, Dict

import httpx


@dataclass(frozen=True)
class TranscriptionResult:
    text: str
    audio_duration_ms: float
    request_prepare_ms: float
    http_round_trip_ms: float
    response_parse_ms: float
    total_ms: float
    server_timing_ms: Dict[str, float]


class SenseVoiceClient:
    def __init__(
        self,
        url: str,
        model: str = "qwen3-asr",
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
        return (await self.transcribe_detailed(pcm)).text

    async def transcribe_detailed(self, pcm: bytes) -> TranscriptionResult:
        total_started_ns = time.perf_counter_ns()
        prepare_started_ns = total_started_ns
        wav_bytes = pcm16_to_wav(pcm)
        headers = {}
        if self.api_key:
            headers["Authorization"] = "Bearer " + self.api_key
        if self.protocol == "octos-json":
            body = {
                "file": base64.b64encode(wav_bytes).decode("ascii"),
                "language": self.language,
                "response_format": "verbose_json",
            }
            request_prepare_ms = elapsed_ms(prepare_started_ns)
            request_started_ns = time.perf_counter_ns()
            response = await self._client.post(
                self.url,
                headers=headers,
                json=body,
            )
        elif self.protocol == "openai-multipart":
            data = {"model": self.model, "response_format": "json"}
            if self.language and self.language != "auto":
                data["language"] = self.language
            request_prepare_ms = elapsed_ms(prepare_started_ns)
            request_started_ns = time.perf_counter_ns()
            response = await self._client.post(
                self.url,
                headers=headers,
                data=data,
                files={"file": ("speech.wav", wav_bytes, "audio/wav")},
            )
        else:
            raise ValueError("Unsupported ASR_PROTOCOL: " + self.protocol)
        http_round_trip_ms = elapsed_ms(request_started_ns)
        response.raise_for_status()
        parse_started_ns = time.perf_counter_ns()
        text = extract_text(response.json()).strip()
        response_parse_ms = elapsed_ms(parse_started_ns)
        return TranscriptionResult(
            text=text,
            audio_duration_ms=len(pcm) / (16_000 * 2) * 1000.0,
            request_prepare_ms=request_prepare_ms,
            http_round_trip_ms=http_round_trip_ms,
            response_parse_ms=response_parse_ms,
            total_ms=elapsed_ms(total_started_ns),
            server_timing_ms=parse_server_timing(response.headers.get("Server-Timing", "")),
        )

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
        raise ValueError("ASR response does not contain text")
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
    raise ValueError("ASR response does not contain text")


def elapsed_ms(started_ns: int) -> float:
    return (time.perf_counter_ns() - started_ns) / 1_000_000


def parse_server_timing(value: str) -> Dict[str, float]:
    """Parse numeric `dur` values from an RFC-compatible Server-Timing header."""
    result: Dict[str, float] = {}
    for entry in value.split(","):
        parts = [part.strip() for part in entry.split(";") if part.strip()]
        if not parts:
            continue
        name = parts[0]
        if not name.replace("-", "").replace("_", "").isalnum():
            continue
        for parameter in parts[1:]:
            if not parameter.startswith("dur="):
                continue
            try:
                duration = float(parameter[4:].strip('"'))
            except ValueError:
                continue
            if duration >= 0:
                result[name] = duration
            break
    return result

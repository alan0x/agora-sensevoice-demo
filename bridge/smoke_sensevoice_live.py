"""Send a real 16 kHz mono WAV file to the configured private ASR server."""

import argparse
import asyncio
import os
import wave
from pathlib import Path

from bridge.sensevoice import SenseVoiceClient


def read_pcm(path: Path) -> bytes:
    with wave.open(str(path), "rb") as wav_file:
        actual = (
            wav_file.getnchannels(),
            wav_file.getsampwidth(),
            wav_file.getframerate(),
        )
        expected = (1, 2, 16_000)
        if actual != expected:
            raise ValueError(
                f"Expected mono 16-bit 16 kHz WAV {expected}, got {actual}"
            )
        return wav_file.readframes(wav_file.getnframes())


async def run(audio_path: Path) -> None:
    client = SenseVoiceClient(
        url=os.getenv(
            "ASR_URL",
            os.getenv(
                "SENSEVOICE_URL",
                "http://127.0.0.1:8080/v1/audio/transcriptions",
            ),
        ),
        language=os.getenv(
            "ASR_LANGUAGE", os.getenv("SENSEVOICE_LANGUAGE", "Chinese")
        ),
        protocol=os.getenv(
            "ASR_PROTOCOL", os.getenv("SENSEVOICE_PROTOCOL", "octos-json")
        ),
    )
    try:
        text = await client.transcribe(read_pcm(audio_path))
    finally:
        await client.close()
    if not text:
        raise RuntimeError("ASR returned an empty transcript")
    print(text)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("audio", type=Path, help="16 kHz mono PCM WAV file")
    args = parser.parse_args()
    asyncio.run(run(args.audio))


if __name__ == "__main__":
    main()

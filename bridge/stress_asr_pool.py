"""Concurrent load test for the local OminiX ASR pool.

Simulates conversational speakers: each virtual speaker sends one utterance
(the provided 16 kHz mono WAV) and then pauses, cycling for the level duration.
Concurrency ramps through the requested levels so you can see where latency
goes vertical.

Example:
    .venv/bin/python stress_asr_pool.py /tmp/test-16k.wav \
        --urls http://127.0.0.1:8080/v1/audio/transcriptions,http://127.0.0.1:8081/v1/audio/transcriptions \
        --levels 5,10,20,35,50 --level-seconds 60
"""

import argparse
import asyncio
import base64
import statistics
import time
from typing import List

import httpx

PAYLOAD_TIMEOUT = 120.0


def percentile(values: List[float], pct: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    rank = (len(ordered) - 1) * pct / 100.0
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    fraction = rank - low
    return ordered[low] + (ordered[high] - ordered[low]) * fraction


async def speaker(
    url: str,
    body: dict,
    stop_at: float,
    pause_seconds: float,
    stagger_seconds: float,
    latencies: List[float],
    errors: List[str],
) -> None:
    async with httpx.AsyncClient(timeout=PAYLOAD_TIMEOUT) as client:
        # Stagger speakers so they do not fire in lockstep.
        await asyncio.sleep(stagger_seconds)
        while time.monotonic() < stop_at:
            started = time.perf_counter()
            try:
                response = await client.post(url, json=body)
                response.raise_for_status()
                latencies.append((time.perf_counter() - started) * 1000.0)
            except Exception as exc:  # noqa: BLE001 - record and keep going
                errors.append(f"{type(exc).__name__}: {exc}")
            await asyncio.sleep(pause_seconds)


async def run_level(
    urls: List[str],
    body: dict,
    concurrency: int,
    level_seconds: float,
    pause_seconds: float,
    audio_ms: float,
) -> None:
    latencies: List[float] = []
    errors: List[str] = []
    stop_at = time.monotonic() + level_seconds
    await asyncio.gather(
        *(
            speaker(
                urls[i % len(urls)],
                body,
                stop_at,
                pause_seconds,
                pause_seconds * i / max(concurrency, 1),
                latencies,
                errors,
            )
            for i in range(concurrency)
        )
    )
    if latencies:
        p50 = percentile(latencies, 50)
        p95 = percentile(latencies, 95)
        mean = statistics.fmean(latencies)
    else:
        p50 = p95 = mean = float("nan")
    print(
        f"concurrency={concurrency:3d}  requests={len(latencies):4d}  "
        f"errors={len(errors):3d}  mean={mean:7.0f}ms  p50={p50:7.0f}ms  "
        f"p95={p95:7.0f}ms  rtf_p95={p95 / audio_ms:5.2f}",
        flush=True,
    )
    if errors:
        sample = errors[0]
        print(f"    first error: {sample}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="OminiX pool concurrency stress test")
    parser.add_argument("audio", help="16 kHz mono WAV used as the utterance")
    parser.add_argument(
        "--urls",
        default=(
            "http://127.0.0.1:8080/v1/audio/transcriptions,"
            "http://127.0.0.1:8081/v1/audio/transcriptions"
        ),
        help="Comma-separated transcription endpoints (the pool)",
    )
    parser.add_argument("--levels", default="5,10,20,35,50")
    parser.add_argument("--level-seconds", type=float, default=60.0)
    parser.add_argument(
        "--pause-seconds",
        type=float,
        default=3.0,
        help="Silence gap between utterances per speaker (duty cycle control)",
    )
    args = parser.parse_args()

    wav_bytes = open(args.audio, "rb").read()
    # WAV header is 44 bytes for canonical 16-bit PCM mono; duration from data size.
    audio_ms = max(1.0, (len(wav_bytes) - 44) / (16_000 * 2) * 1000.0)
    body = {
        "file": base64.b64encode(wav_bytes).decode("ascii"),
        "language": "Chinese",
        "response_format": "verbose_json",
    }
    urls = [url.strip() for url in args.urls.split(",") if url.strip()]
    levels = [int(level) for level in args.levels.split(",") if level.strip()]

    print(f"urls={len(urls)} utterance={audio_ms:.0f}ms pause={args.pause_seconds}s")
    for level in levels:
        asyncio.run(
            run_level(urls, body, level, args.level_seconds, args.pause_seconds, audio_ms)
        )


if __name__ == "__main__":
    main()

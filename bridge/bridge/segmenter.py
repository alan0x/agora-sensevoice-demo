"""Small dependency-free PCM16 utterance segmenter.

The thresholds are deliberately simple and observable for a demo. Production
deployments can replace this class with the Agora VAD result or Silero VAD
without changing the bridge protocol.
"""

from collections import deque
from dataclasses import dataclass
import math
import struct
import time
from typing import Deque, List


@dataclass(frozen=True)
class SegmentEvent:
    kind: str
    pcm: bytes
    utterance_index: int
    audio_duration_ms: float
    endpoint_ms: float
    boundary_reason: str
    speech_started_ns: int
    last_voice_ns: int
    emitted_ns: int


@dataclass(frozen=True)
class SegmenterConfig:
    sample_rate: int = 16_000
    threshold_dbfs: float = -38.0
    pre_roll_ms: int = 240
    speech_start_ms: int = 140
    speech_end_ms: int = 650
    partial_interval_ms: int = 1_100
    max_utterance_ms: int = 15_000


class PcmSegmenter:
    def __init__(self, config: SegmenterConfig = SegmenterConfig()) -> None:
        self.config = config
        self._pre_roll: Deque[bytes] = deque()
        self._pre_roll_duration_ms = 0.0
        self._active = False
        self._speech_candidate_ms = 0.0
        self._silence_ms = 0.0
        self._utterance = bytearray()
        self._utterance_ms = 0.0
        self._last_partial_ms = 0.0
        self._speech_candidate_started_ns = 0
        self._speech_started_ns = 0
        self._last_voice_ns = 0
        self._utterance_index = 0
        self._next_utterance_index = 1

    @property
    def active(self) -> bool:
        return self._active

    def feed(self, pcm: bytes, received_ns: int | None = None) -> List[SegmentEvent]:
        if not pcm or len(pcm) % 2:
            return []
        if received_ns is None:
            received_ns = time.perf_counter_ns()
        frame_ms = len(pcm) / (self.config.sample_rate * 2) * 1000.0
        speaking = pcm_dbfs(pcm) >= self.config.threshold_dbfs

        if not self._active:
            self._push_pre_roll(pcm, frame_ms)
            if speaking:
                if self._speech_candidate_ms == 0.0:
                    self._speech_candidate_started_ns = received_ns
                self._speech_candidate_ms += frame_ms
                self._last_voice_ns = received_ns
            else:
                self._speech_candidate_ms = 0.0
                self._speech_candidate_started_ns = 0
            if self._speech_candidate_ms < self.config.speech_start_ms:
                return []
            self._active = True
            self._utterance_index = self._next_utterance_index
            self._next_utterance_index += 1
            self._speech_started_ns = self._speech_candidate_started_ns or received_ns
            self._utterance = bytearray().join(self._pre_roll)
            self._utterance_ms = self._pre_roll_duration_ms
            self._last_partial_ms = self._utterance_ms
            self._silence_ms = 0.0
            self._pre_roll.clear()
            self._pre_roll_duration_ms = 0.0
            return []

        self._utterance.extend(pcm)
        self._utterance_ms += frame_ms
        if speaking:
            self._silence_ms = 0.0
            self._last_voice_ns = received_ns
        else:
            self._silence_ms += frame_ms

        if self._utterance_ms >= self.config.max_utterance_ms:
            return [self._finish("max-duration", received_ns)]
        if self._silence_ms >= self.config.speech_end_ms:
            return [self._finish("silence", received_ns)]
        if (
            speaking
            and self._utterance_ms - self._last_partial_ms
            >= self.config.partial_interval_ms
        ):
            self._last_partial_ms = self._utterance_ms
            return [self._snapshot("partial", "interval", received_ns)]
        return []

    def commit(self) -> List[SegmentEvent]:
        if not self._active or not self._utterance:
            return []
        return [self._finish("manual", time.perf_counter_ns())]

    def reset(self) -> None:
        self._pre_roll.clear()
        self._pre_roll_duration_ms = 0.0
        self._active = False
        self._speech_candidate_ms = 0.0
        self._silence_ms = 0.0
        self._utterance.clear()
        self._utterance_ms = 0.0
        self._last_partial_ms = 0.0
        self._speech_candidate_started_ns = 0
        self._speech_started_ns = 0
        self._last_voice_ns = 0
        self._utterance_index = 0

    def _push_pre_roll(self, pcm: bytes, duration_ms: float) -> None:
        self._pre_roll.append(pcm)
        self._pre_roll_duration_ms += duration_ms
        while (
            self._pre_roll
            and self._pre_roll_duration_ms > self.config.pre_roll_ms
        ):
            removed = self._pre_roll.popleft()
            self._pre_roll_duration_ms -= (
                len(removed) / (self.config.sample_rate * 2) * 1000.0
            )

    def _snapshot(self, kind: str, reason: str, emitted_ns: int) -> SegmentEvent:
        if reason == "silence":
            endpoint_ms = self._silence_ms
        elif self._last_voice_ns:
            endpoint_ms = max(0.0, (emitted_ns - self._last_voice_ns) / 1_000_000)
        else:
            endpoint_ms = 0.0
        return SegmentEvent(
            kind=kind,
            pcm=bytes(self._utterance),
            utterance_index=self._utterance_index,
            audio_duration_ms=self._utterance_ms,
            endpoint_ms=endpoint_ms,
            boundary_reason=reason,
            speech_started_ns=self._speech_started_ns,
            last_voice_ns=self._last_voice_ns,
            emitted_ns=emitted_ns,
        )

    def _finish(self, reason: str, emitted_ns: int) -> SegmentEvent:
        event = self._snapshot("final", reason, emitted_ns)
        self.reset()
        return event


def pcm_dbfs(pcm: bytes) -> float:
    count = len(pcm) // 2
    if count == 0:
        return -96.0
    square_sum = 0
    for (sample,) in struct.iter_unpack("<h", pcm):
        square_sum += sample * sample
    rms = math.sqrt(square_sum / count)
    if rms < 1.0:
        return -96.0
    return 20.0 * math.log10(rms / 32768.0)

"""Small dependency-free PCM16 utterance segmenter.

The thresholds are deliberately simple and observable for a demo. Production
deployments can replace this class with the Agora VAD result or Silero VAD
without changing the bridge protocol.
"""

from collections import deque
from dataclasses import dataclass
import math
import struct
from typing import Deque, List


@dataclass(frozen=True)
class SegmentEvent:
    kind: str
    pcm: bytes


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

    @property
    def active(self) -> bool:
        return self._active

    def feed(self, pcm: bytes) -> List[SegmentEvent]:
        if not pcm or len(pcm) % 2:
            return []
        frame_ms = len(pcm) / (self.config.sample_rate * 2) * 1000.0
        speaking = pcm_dbfs(pcm) >= self.config.threshold_dbfs

        if not self._active:
            self._push_pre_roll(pcm, frame_ms)
            self._speech_candidate_ms = (
                self._speech_candidate_ms + frame_ms if speaking else 0.0
            )
            if self._speech_candidate_ms < self.config.speech_start_ms:
                return []
            self._active = True
            self._utterance = bytearray().join(self._pre_roll)
            self._utterance_ms = self._pre_roll_duration_ms
            self._last_partial_ms = self._utterance_ms
            self._silence_ms = 0.0
            self._pre_roll.clear()
            self._pre_roll_duration_ms = 0.0
            return []

        self._utterance.extend(pcm)
        self._utterance_ms += frame_ms
        self._silence_ms = 0.0 if speaking else self._silence_ms + frame_ms

        if self._utterance_ms >= self.config.max_utterance_ms:
            return [self._finish()]
        if self._silence_ms >= self.config.speech_end_ms:
            return [self._finish()]
        if (
            speaking
            and self._utterance_ms - self._last_partial_ms
            >= self.config.partial_interval_ms
        ):
            self._last_partial_ms = self._utterance_ms
            return [SegmentEvent("partial", bytes(self._utterance))]
        return []

    def commit(self) -> List[SegmentEvent]:
        if not self._active or not self._utterance:
            return []
        return [self._finish()]

    def reset(self) -> None:
        self._pre_roll.clear()
        self._pre_roll_duration_ms = 0.0
        self._active = False
        self._speech_candidate_ms = 0.0
        self._silence_ms = 0.0
        self._utterance.clear()
        self._utterance_ms = 0.0
        self._last_partial_ms = 0.0

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

    def _finish(self) -> SegmentEvent:
        event = SegmentEvent("final", bytes(self._utterance))
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

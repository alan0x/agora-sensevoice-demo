import math
import struct
import unittest

from bridge.segmenter import PcmSegmenter, SegmenterConfig, pcm_dbfs


def tone(duration_ms, amplitude=8_000, sample_rate=16_000):
    samples = int(sample_rate * duration_ms / 1000)
    values = (
        int(amplitude * math.sin(2 * math.pi * 440 * index / sample_rate))
        for index in range(samples)
    )
    return b"".join(struct.pack("<h", value) for value in values)


def silence(duration_ms, sample_rate=16_000):
    return bytes(int(sample_rate * duration_ms / 1000) * 2)


class SegmenterTests(unittest.TestCase):
    def test_dbfs_distinguishes_tone_and_silence(self):
        self.assertGreater(pcm_dbfs(tone(20)), -38)
        self.assertLess(pcm_dbfs(silence(20)), -90)

    def test_speech_then_silence_emits_final(self):
        segmenter = PcmSegmenter(
            SegmenterConfig(
                speech_start_ms=100,
                speech_end_ms=200,
                partial_interval_ms=5_000,
            )
        )
        events = []
        for _ in range(8):
            events.extend(segmenter.feed(tone(20)))
        for _ in range(11):
            events.extend(segmenter.feed(silence(20)))
        self.assertEqual([event.kind for event in events], ["final"])
        self.assertGreater(len(events[0].pcm), 0)

    def test_manual_commit(self):
        segmenter = PcmSegmenter(SegmenterConfig(speech_start_ms=40))
        segmenter.feed(tone(20))
        segmenter.feed(tone(20))
        events = segmenter.commit()
        self.assertEqual([event.kind for event in events], ["final"])


if __name__ == "__main__":
    unittest.main()

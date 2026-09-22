import unittest

from bridge.tts import PcmUpsampler2x


def pcm(samples):
    return b"".join(int(s).to_bytes(2, "little", signed=True) for s in samples)


def unpack(data):
    return [
        int.from_bytes(data[i : i + 2], "little", signed=True)
        for i in range(0, len(data), 2)
    ]


class PcmUpsampler2xTest(unittest.TestCase):
    def test_doubles_with_linear_interpolation(self):
        upsampler = PcmUpsampler2x()
        out = unpack(upsampler.feed(pcm([0, 100, 200])))
        # s0, mid(s0,s1), s1, mid(s1,s2), s2
        self.assertEqual(out, [0, 50, 100, 150, 200])

    def test_negative_samples_round_toward_zero(self):
        upsampler = PcmUpsampler2x()
        out = unpack(upsampler.feed(pcm([-100, -200])))
        self.assertEqual(out, [-100, -150, -200])

    def test_chunk_boundaries_stay_continuous(self):
        whole = PcmUpsampler2x().feed(pcm([10, 20, 30, 40]))
        split = PcmUpsampler2x()
        part1 = split.feed(pcm([10, 20]))
        part2 = split.feed(pcm([30, 40]))
        self.assertEqual(part1 + part2, whole)

    def test_odd_byte_is_carried(self):
        upsampler = PcmUpsampler2x()
        data = pcm([1000, 2000])
        out1 = upsampler.feed(data[:3])  # 1.5 samples
        out2 = upsampler.feed(data[3:])
        self.assertEqual(unpack(out1 + out2), [1000, 1500, 2000])

    def test_empty_feed_returns_empty(self):
        self.assertEqual(PcmUpsampler2x().feed(b""), b"")


if __name__ == "__main__":
    unittest.main()

import unittest

from bridge.livekit_receiver import slice_push_frames


class SlicePushFramesTest(unittest.TestCase):
    def test_exact_multiple(self):
        # 48 kHz mono: 10 ms = 960 bytes
        pcm = b"\x01" * 1920
        frames = slice_push_frames(pcm, 48_000, 1)
        self.assertEqual(len(frames), 2)
        self.assertTrue(all(len(frame) == 960 for frame in frames))

    def test_tail_is_silence_padded(self):
        pcm = b"\x02" * 1000  # 960 + 40
        frames = slice_push_frames(pcm, 48_000, 1)
        self.assertEqual(len(frames), 2)
        self.assertEqual(len(frames[1]), 960)
        self.assertEqual(frames[1][:40], b"\x02" * 40)
        self.assertEqual(frames[1][40:], b"\x00" * 920)

    def test_empty_input(self):
        self.assertEqual(slice_push_frames(b"", 48_000, 1), [])

    def test_16khz_frame_size(self):
        frames = slice_push_frames(b"\x03" * 320, 16_000, 1)
        self.assertEqual(len(frames), 1)
        self.assertEqual(len(frames[0]), 320)


if __name__ == "__main__":
    unittest.main()

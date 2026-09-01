import unittest

from bridge.sensevoice import extract_text, pcm16_to_wav


class SenseVoiceAdapterTests(unittest.TestCase):
    def test_extracts_common_response_shapes(self):
        self.assertEqual(extract_text({"text": "你好"}), "你好")
        self.assertEqual(extract_text({"data": {"text": "世界"}}), "世界")
        self.assertEqual(extract_text([{"transcript": "测试"}]), "测试")

    def test_pcm_is_wrapped_as_wav(self):
        payload = pcm16_to_wav(bytes(320))
        self.assertEqual(payload[:4], b"RIFF")
        self.assertIn(b"WAVE", payload[:16])


if __name__ == "__main__":
    unittest.main()

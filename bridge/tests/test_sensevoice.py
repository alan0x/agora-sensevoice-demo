import base64
import json
import unittest

import httpx

from bridge.sensevoice import SenseVoiceClient, extract_text, pcm16_to_wav


class SenseVoiceAdapterTests(unittest.TestCase):
    def test_extracts_common_response_shapes(self):
        self.assertEqual(extract_text({"text": "你好"}), "你好")
        self.assertEqual(extract_text({"data": {"text": "世界"}}), "世界")
        self.assertEqual(extract_text([{"transcript": "测试"}]), "测试")

    def test_pcm_is_wrapped_as_wav(self):
        payload = pcm16_to_wav(bytes(320))
        self.assertEqual(payload[:4], b"RIFF")
        self.assertIn(b"WAVE", payload[:16])


class SenseVoiceHttpTests(unittest.IsolatedAsyncioTestCase):
    async def test_uses_octos_json_base64_contract(self):
        captured = {}

        def handle(request):
            captured.update(json.loads(request.content))
            return httpx.Response(200, json={"text": "真实识别", "rejected": False})

        client = SenseVoiceClient("http://sensevoice/v1/audio/transcriptions")
        await client._client.aclose()
        client._client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        try:
            text = await client.transcribe(bytes(320))
        finally:
            await client.close()

        self.assertEqual(text, "真实识别")
        self.assertEqual(captured["language"], "Chinese")
        self.assertEqual(captured["response_format"], "verbose_json")
        self.assertEqual(base64.b64decode(captured["file"])[:4], b"RIFF")


if __name__ == "__main__":
    unittest.main()

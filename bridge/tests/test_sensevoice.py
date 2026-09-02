import base64
import json
import unittest

import httpx

from bridge.sensevoice import (
    SenseVoiceClient,
    extract_text,
    parse_server_timing,
    pcm16_to_wav,
)


class SenseVoiceAdapterTests(unittest.TestCase):
    def test_extracts_common_response_shapes(self):
        self.assertEqual(extract_text({"text": "你好"}), "你好")
        self.assertEqual(extract_text({"data": {"text": "世界"}}), "世界")
        self.assertEqual(extract_text([{"transcript": "测试"}]), "测试")

    def test_pcm_is_wrapped_as_wav(self):
        payload = pcm16_to_wav(bytes(320))
        self.assertEqual(payload[:4], b"RIFF")
        self.assertIn(b"WAVE", payload[:16])

    def test_parses_server_timing_durations(self):
        self.assertEqual(
            parse_server_timing('queue;dur=2.5, decode;dur=31, inference;dur="428.2"'),
            {"queue": 2.5, "decode": 31.0, "inference": 428.2},
        )


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

    async def test_returns_detailed_request_timings(self):
        def handle(request):
            return httpx.Response(
                200,
                headers={"Server-Timing": "inference;dur=12.5"},
                json={"text": "带指标的识别"},
            )

        client = SenseVoiceClient("http://sensevoice/v1/audio/transcriptions")
        await client._client.aclose()
        client._client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        try:
            result = await client.transcribe_detailed(bytes(320))
        finally:
            await client.close()

        self.assertEqual(result.text, "带指标的识别")
        self.assertAlmostEqual(result.audio_duration_ms, 10.0)
        self.assertEqual(result.server_timing_ms, {"inference": 12.5})
        self.assertGreaterEqual(result.total_ms, 0.0)


if __name__ == "__main__":
    unittest.main()

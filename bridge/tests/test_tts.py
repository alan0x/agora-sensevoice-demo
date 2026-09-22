import unittest

from bridge.tts import PcmUpsampler2x, normalize_tts_text


class NormalizeTtsTextTest(unittest.TestCase):
    def test_chinese_text_avoids_comma_splitting_by_default(self):
        self.assertEqual(
            normalize_tts_text("你好，世界！好吗？"),
            "你好,世界！好吗？",
        )
        self.assertEqual(
            normalize_tts_text("第一、第二；第三，第四。"),
            "第一,第二;第三,第四。",
        )

    def test_chinese_multi_line_paragraphs_are_collapsed(self):
        text = "第一行。\n第二行，继续。\n第三行。"
        self.assertEqual(
            normalize_tts_text(text),
            "第一行。 第二行,继续。 第三行。",
        )

    def test_chinese_text_legacy_full_width_when_disabled(self):
        self.assertEqual(
            normalize_tts_text("你好,世界!好吗?", avoid_comma_split=False),
            "你好，世界！好吗？",
        )

    def test_ascii_text_is_untouched(self):
        self.assertEqual(normalize_tts_text("hello, world!"), "hello, world!")


class TtsClientConfigTest(unittest.TestCase):
    def test_default_config_has_sampling_parameters(self):
        from bridge.tts import TtsClient

        client = TtsClient("http://127.0.0.1:8090/v1/audio/speech")
        self.assertEqual(client.temperature, 0.2)
        self.assertEqual(client.top_p, 0.8)
        self.assertEqual(client.seed, 42)
        self.assertTrue(client.avoid_comma_split)

    def test_custom_sampling_config(self):
        from bridge.tts import TtsClient

        client = TtsClient(
            "http://127.0.0.1:8090/v1/audio/speech",
            temperature=0.0,
            top_p=0.9,
            seed=42,
            avoid_comma_split=False,
        )
        self.assertEqual(client.temperature, 0.0)
        self.assertEqual(client.top_p, 0.9)
        self.assertEqual(client.seed, 42)
        self.assertFalse(client.avoid_comma_split)


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


class FakeTtsClient:
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks

    async def stream_pcm(self, text: str, voice: str = None):
        for chunk in self.chunks:
            yield chunk


class FakeReceiver:
    def __init__(self) -> None:
        self.pushed_frames: list[bytes] = []
        self.cleared = False

    def push_pcm(self, pcm: bytes, sample_rate: int = 48000, channels: int = 1) -> bool:
        self.pushed_frames.append(bytes(pcm))
        return True

    def clear_audio_buffer(self) -> None:
        self.cleared = True

    def stop(self) -> None:
        pass


class TtsPlayoutPacerTest(unittest.IsolatedAsyncioTestCase):
    async def test_speak_pushes_20ms_frames_and_emits_finished(self):
        from bridge.main import RealSession, TTS_TARGET_RATE

        emitted = []

        async def emit(payload):
            emitted.append(payload)

        session = RealSession(
            session_id="test-session",
            agora={"appId": "app", "channel": "chan", "token": "tok", "uid": 9001},
            asr_url="http://localhost:8080",
            emit=emit,
            tts_url="http://localhost:8090/v1/audio/speech",
        )
        fake_receiver = FakeReceiver()
        session.receiver = fake_receiver

        # Create 200 ms of 24 kHz audio (24000 * 2 * 0.2 = 9600 bytes)
        # After 2x upsampling, becomes 400 ms of 48 kHz audio (19200 bytes = 10 frames of 20 ms)
        raw_chunk = b"\x01\x00" * 4800
        session.tts = FakeTtsClient([raw_chunk])

        await session.speak("你好")
        await session.tts_task

        # Verify events
        self.assertEqual(emitted[0]["type"], "tts.started")
        self.assertEqual(emitted[0]["characters"], 2)
        self.assertEqual(emitted[1]["type"], "tts.finished")

        # Verify each frame is exactly 20 ms = 1920 bytes
        frame_bytes = int(TTS_TARGET_RATE * 2 * 0.02)
        self.assertEqual(frame_bytes, 1920)
        self.assertGreater(len(fake_receiver.pushed_frames), 0)
        for frame in fake_receiver.pushed_frames:
            self.assertEqual(len(frame), frame_bytes)

    async def test_barge_in_cancels_previous_and_clears_buffer(self):
        import asyncio
        from bridge.main import RealSession

        emitted = []

        async def emit(payload):
            emitted.append(payload)

        session = RealSession(
            session_id="test-session",
            agora={"appId": "app", "channel": "chan", "token": "tok", "uid": 9001},
            asr_url="http://localhost:8080",
            emit=emit,
            tts_url="http://localhost:8090/v1/audio/speech",
        )
        fake_receiver = FakeReceiver()
        session.receiver = fake_receiver

        # A long stream: 10 chunks of 100 ms each
        class SlowTtsClient:
            async def stream_pcm(self, text, voice=None):
                for _ in range(10):
                    yield b"\x01\x00" * 2400
                    await asyncio.sleep(0.05)

        session.tts = SlowTtsClient()

        # Start first speak
        await session.speak("第一句很长的话")
        first_task = session.tts_task
        await asyncio.sleep(0.02)

        # Barge-in with a second speak
        session.tts = FakeTtsClient([b"\x01\x00" * 2400])
        await session.speak("第二句短话")

        self.assertTrue(first_task.done())
        self.assertTrue(fake_receiver.cleared)
        await session.tts_task


if __name__ == "__main__":
    unittest.main()

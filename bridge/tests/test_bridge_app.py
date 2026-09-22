import asyncio
import unittest

from bridge.main import BridgeApp, MockSession, parse_asr_urls


class ParseAsrUrlsTest(unittest.TestCase):
    def test_comma_separated_pool(self):
        urls = parse_asr_urls(
            " http://127.0.0.1:8080/v1/audio/transcriptions ,"
            "http://127.0.0.1:8081/v1/audio/transcriptions , "
        )
        self.assertEqual(
            urls,
            [
                "http://127.0.0.1:8080/v1/audio/transcriptions",
                "http://127.0.0.1:8081/v1/audio/transcriptions",
            ],
        )

    def test_fallback_single_url(self):
        self.assertEqual(parse_asr_urls("", "http://x:1/t"), ["http://x:1/t"])
        self.assertEqual(parse_asr_urls(" , ", " http://x:1/t "), ["http://x:1/t"])

    def test_empty_everything(self):
        self.assertEqual(parse_asr_urls("", ""), [])


class BridgeAppMultiSessionTest(unittest.IsolatedAsyncioTestCase):
    def make_app(self, **kwargs):
        app = BridgeApp("mock", "ws://localhost:9/ws/bridge", "s" * 16, **kwargs)
        sent = []

        async def record(payload):
            sent.append(payload)

        app.emit = record
        return app, sent

    async def test_multiple_sessions_coexist(self):
        app, sent = self.make_app()
        await app.handle({"type": "session.start", "sessionId": "s1"})
        await app.handle({"type": "session.start", "sessionId": "s2"})

        self.assertEqual(set(app.sessions), {"s1", "s2"})
        ready = [p["sessionId"] for p in sent if p["type"] == "session.ready"]
        self.assertEqual(ready, ["s1", "s2"])

        # Stopping one session must not disturb the other.
        await app.handle({"type": "session.stop", "sessionId": "s1"})
        self.assertEqual(set(app.sessions), {"s2"})
        self.assertIsInstance(app.sessions["s2"], MockSession)

        closed = [p["sessionId"] for p in sent if p["type"] == "session.closed"]
        self.assertEqual(closed, ["s1"])
        await app.stop_all_sessions()

    async def test_commit_is_routed_by_session_id(self):
        app, sent = self.make_app()
        await app.handle({"type": "session.start", "sessionId": "s1"})
        await app.handle({"type": "session.start", "sessionId": "s2"})

        await app.handle({"type": "utterance.commit", "sessionId": "s2"})
        finals = [
            p for p in sent
            if p["type"] == "asr.final" and "手动断句成功" in p.get("text", "")
        ]
        self.assertEqual([p["sessionId"] for p in finals], ["s2"])

        # Unknown session ids are ignored without raising.
        await app.handle({"type": "utterance.commit", "sessionId": "ghost"})
        await app.handle({"type": "session.stop", "sessionId": "ghost"})
        await app.stop_all_sessions()

    async def test_max_sessions_rejects_overflow(self):
        app, sent = self.make_app(max_sessions=1)
        await app.handle({"type": "session.start", "sessionId": "s1"})
        await app.handle({"type": "session.start", "sessionId": "s2"})

        self.assertEqual(set(app.sessions), {"s1"})
        errors = [p for p in sent if p["type"] == "asr.error"]
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["sessionId"], "s2")
        await app.stop_all_sessions()

    async def test_restart_same_session_id_replaces_it(self):
        app, sent = self.make_app()
        await app.handle({"type": "session.start", "sessionId": "s1"})
        first = app.sessions["s1"]
        await app.handle({"type": "session.start", "sessionId": "s1"})
        self.assertIsNot(app.sessions["s1"], first)
        self.assertEqual(len(app.sessions), 1)
        await app.stop_all_sessions()

    async def test_asr_url_round_robin(self):
        app, _ = self.make_app(asr_urls=["http://a/", "http://b/"])
        picks = [app._next_asr_url() for _ in range(5)]
        self.assertEqual(
            picks, ["http://a/", "http://b/", "http://a/", "http://b/", "http://a/"]
        )

    async def test_tts_speak_routed_by_session_id(self):
        app, sent = self.make_app()
        await app.handle({"type": "session.start", "sessionId": "s1"})
        await app.handle({"type": "tts.speak", "sessionId": "s1", "text": "你好"})

        started = [p for p in sent if p["type"] == "tts.started"]
        finished = [p for p in sent if p["type"] == "tts.finished"]
        self.assertEqual(len(started), 1)
        self.assertEqual(started[0]["sessionId"], "s1")
        self.assertEqual(started[0]["characters"], 2)
        self.assertEqual(len(finished), 1)

        # Unknown session ids are ignored without raising.
        await app.handle({"type": "tts.speak", "sessionId": "ghost", "text": "x"})
        self.assertEqual(
            len([p for p in sent if p["type"] == "tts.started"]), 1
        )
        await app.stop_all_sessions()


if __name__ == "__main__":
    unittest.main()

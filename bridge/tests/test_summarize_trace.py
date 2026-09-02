import unittest

from summarize_trace import percentile, summarize_report


class TraceSummaryTests(unittest.TestCase):
    def test_percentile_uses_linear_interpolation(self):
        self.assertEqual(percentile([100, 200, 300], 0.5), 200)
        self.assertEqual(percentile([], 0.95), None)

    def test_markdown_contains_stage_percentiles(self):
        report = {
            "exportedAt": "2026-09-01T00:00:00Z",
            "observations": [
                {
                    "complete": True,
                    "metrics": {
                        "summary": {"estimatedEndToEndMs": 1050},
                        "browser": {
                            "speechEndToFinalMs": 1000,
                            "manualCommitToFinalMs": 900,
                        },
                        "bridge": {"endpointMs": 650, "asrTotalMs": 300},
                        "asr": {"rtf": 0.1},
                    },
                },
                {
                    "complete": True,
                    "metrics": {
                        "summary": {"estimatedEndToEndMs": 1250},
                        "browser": {"speechEndToFinalMs": 1200},
                        "bridge": {"endpointMs": 660, "asrTotalMs": 400},
                        "asr": {"rtf": 0.2},
                    },
                },
            ],
        }
        markdown = summarize_report(report)
        self.assertIn("最终识别样本：2", markdown)
        self.assertIn("| 端到端链路估算 | 2 | 1150.0ms |", markdown)
        self.assertIn("| 立即断句到最终文本 | 1 | 900.0ms |", markdown)
        self.assertIn("| 客户端音量估算（仅参考） | 2 | 1100.0ms |", markdown)
        self.assertIn("| RTF | 2 | 0.150 |", markdown)


if __name__ == "__main__":
    unittest.main()

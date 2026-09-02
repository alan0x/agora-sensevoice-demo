#!/usr/bin/env python3
"""Turn a browser-exported ASR trace JSON file into a Markdown latency report."""

import argparse
import json
from pathlib import Path
from statistics import fmean
from typing import Any, Iterable, Sequence


METRICS = (
    ("端到端链路估算", ("summary", "estimatedEndToEndMs"), "ms"),
    ("立即断句到最终文本", ("browser", "manualCommitToFinalMs"), "ms"),
    ("客户端音量估算（仅参考）", ("browser", "speechEndToFinalMs"), "ms"),
    ("开始说话到最终文本", ("browser", "speechStartToFinalMs"), "ms"),
    ("开始说话到首个 partial", ("browser", "firstPartialMs"), "ms"),
    ("Agora 网络传输", ("agora", "networkTransportDelayMs"), "ms"),
    ("Agora 抖动缓冲", ("agora", "jitterBufferDelayMs"), "ms"),
    ("Bridge 断句", ("bridge", "endpointMs"), "ms"),
    ("Bridge 音频排队", ("bridge", "audioQueueMs"), "ms"),
    ("ASR 请求准备", ("bridge", "asrRequestPrepareMs"), "ms"),
    ("OminiX HTTP 往返", ("bridge", "asrHttpRoundTripMs"), "ms"),
    ("OminiX 总处理", ("bridge", "asrTotalMs"), "ms"),
    ("VPS 转发排队", ("vps", "relayQueueMs"), "ms"),
    ("文字交付 ACK RTT", ("delivery", "vpsBrowserAckRttMs"), "ms"),
    ("RTF", ("asr", "rtf"), "ratio"),
    ("Agora 收音丢包", ("agora", "audioLossRatePercent"), "%"),
)


def nested_number(value: Any, path: Sequence[str]) -> float | None:
    current = value
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    if isinstance(current, bool) or not isinstance(current, (int, float)):
        return None
    return float(current)


def percentile(values: Sequence[float], ratio: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * ratio
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def metric_values(observations: Iterable[dict[str, Any]], path: Sequence[str]) -> list[float]:
    values = []
    for observation in observations:
        number = nested_number(observation.get("metrics", {}), path)
        if number is not None:
            values.append(number)
    return values


def format_value(value: float | None, unit: str) -> str:
    if value is None:
        return "—"
    if unit == "ratio":
        return f"{value:.3f}"
    return f"{value:.1f}{unit}"


def summarize_report(report: dict[str, Any]) -> str:
    observations = [
        observation
        for observation in report.get("observations", [])
        if isinstance(observation, dict) and observation.get("complete") is True
    ]
    lines = [
        "# Agora × OminiX ASR 延时报告",
        "",
        f"- 导出时间：{report.get('exportedAt', '未知')}",
        f"- 最终识别样本：{len(observations)}",
        "- 跨网络单向延时为 RTT/2 估算值；其余阶段使用所在进程的单调时钟。",
        "",
        "| 指标 | 样本 | 平均 | P50 | P95 | 最大 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for label, path, unit in METRICS:
        values = metric_values(observations, path)
        average = fmean(values) if values else None
        lines.append(
            "| "
            + " | ".join(
                [
                    label,
                    str(len(values)),
                    format_value(average, unit),
                    format_value(percentile(values, 0.5), unit),
                    format_value(percentile(values, 0.95), unit),
                    format_value(max(values) if values else None, unit),
                ]
            )
            + " |"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trace_json", type=Path, help="JSON downloaded from the ASR page")
    parser.add_argument("--output", type=Path, help="optional Markdown output path")
    args = parser.parse_args()
    report = json.loads(args.trace_json.read_text(encoding="utf-8"))
    markdown = summarize_report(report)
    if args.output:
        args.output.write_text(markdown, encoding="utf-8")
    else:
        print(markdown, end="")


if __name__ == "__main__":
    main()

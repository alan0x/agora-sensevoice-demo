# Private Realtime Speech — LiveKit/Agora × Qwen3-ASR/TTS

> A self-hosted, production-shaped reference architecture for real-time speech
> recognition and synthesis: browser audio flows over RTC into a private
> inference host running Qwen3-ASR / Qwen3-TTS on Apple Silicon (via
> [OminiX-API](https://github.com/OminiX-ai/OminiX-API)), and results stream
> back over a lightweight control plane. No audio ever leaves your own
> infrastructure.

一个面向隐私与自建算力场景的**实时语音服务参考实现**：浏览器麦克风音频经 RTC 实时传输到自有推理主机（Apple Silicon 上的 Qwen3-ASR/TTS），转写文字通过轻量控制面毫秒级回传；支持把合成语音实时播回通话频道。适合想在数据不出自有基础设施的前提下构建语音交互产品的团队。

## 它能解决什么问题

- **隐私合规**：音频与识别/合成推理全程在自有基础设施内，不经过任何第三方 AI API;
- **成本可控**：推理跑在 Apple Silicon 本机（M 系列）上，按并发线性扩展实例池，无按分钟计费；
- **RTC 传输可自选**：自托管 LiveKit SFU（完全无第三方依赖）或 Agora 云，一键切换；
- **生产形态而非玩具**：动态短期凭证、会话鉴权、容量准入、并发实例池、端到端延时瀑布观测一应俱全。

## 架构

```text
浏览器(Web SDK) ──RTC 音频──> RTC 层(LiveKit 自托管 SFU / Agora 云)
                                   │ 16kHz PCM
                                   ▼
                     推理主机 Bridge(Python)──HTTP──> OminiX 实例池(Qwen3-ASR/TTS)
                                   │ 出站 WSS
浏览器(Web SDK) <──文字/事件 WSS── 控制面(Rust + Salvo,鉴权/会话/凭证签发)
```

- **RTC 层**只转发音频；推理主机和模型不暴露任何公网入站端口，Bridge 全部主动出站连接；
- **控制面**不承载音频与推理：只负责鉴权、按会话动态签发短期 RTC 凭证、转发识别事件；
- **断句**在 Bridge 内完成（可替换的 VAD 端点检测），TTS 与 ASR 分实例隔离，互不阻塞。

## 组件

| 目录 | 内容 |
|---|---|
| `control-plane/` | Rust + Salvo：鉴权、会话管理、RTC 凭证签发（AccessToken2 / LiveKit JWT)、WebSocket 转发、静态站点（主页 / 文档 / Playground) |
| `bridge/` | Python:RTC 收发（Agora Server SDK 或 livekit-rtc)、断句器、ASR 实例池轮询、TTS 合成与播放、压力测试工具 |
| `deploy/` | Docker Compose、环境变量模板、Nginx 反代示例 |
| `docs/PROTOCOL.md` | 控制面协议（REST / WebSocket 事件 / metrics 约定） |

## 功能特性

- 多会话并发：`SESSION_CAPACITY` 准入 + ASR 实例池 round-robin,Apple Silicon 单机实测 90+ 并发会话;
- TTS 双向链路：文字经控制面下发，Bridge 合成后发布进 RTC 频道播放，支持音色/语速/语气参数与 barge-in;
- 延时观测：按语句关联 Agora 网络、断句、推理、转发、渲染各阶段耗时，P50/P95 统计与 JSON 导出;
- Mock 模式：无 RTC 凭据也可开发调试控制面与前端。

## 快速开始（Mock 模式）

无需任何凭据即可体验完整控制面与前端：

```bash
cp deploy/.env.example deploy/.env   # 将 DEMO_MODE=true
docker compose --env-file deploy/.env -f deploy/docker-compose.yml --profile mock up -d --build
# 打开 http://localhost:18080,Mock Bridge 会推送模拟识别结果
```

真实链路需要：一台 Apple Silicon 主机运行 [OminiX-API](https://github.com/OminiX-ai/OminiX-API)(Qwen3-ASR/TTS 模型）,RTC 层选择自托管 [LiveKit](https://github.com/livekit/livekit) SFU 或 Agora 项目，按 `deploy/.env.example` 与 `bridge/.env.example` 配置后分别启动控制面与 Bridge(`bridge/start-real.sh`)。

## 本地检查

```bash
make check   # cargo test + clippy, python unittest, JS/shell 语法检查
```

要求：Rust 1.96+、Python 3.10+、Node.js。

## 参考

- [OminiX-API](https://github.com/OminiX-ai/OminiX-API) — Apple Silicon 上的 OpenAI 兼容推理服务
- [LiveKit](https://github.com/livekit/livekit) — 开源 WebRTC SFU
- [声网 Agora Web SDK](https://doc.shengwang.cn/doc/rtc/javascript/resources) / [Agora Python Server SDK](https://github.com/AgoraIO-Extensions/Agora-Python-Server-SDK)
- [Salvo](https://docs.rs/salvo/latest/salvo/)

## License

MIT，详见 [LICENSE](LICENSE)。

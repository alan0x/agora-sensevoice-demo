# Agora × OminiX private ASR demo

一个直接运行真实 ASR 的端到端 Demo：客户端集成 Agora Web SDK，把麦克风音频发进 RTC 频道；当前电脑上的 Bridge 以 Agora Server SDK 入会并接收 16 kHz 单声道 PCM，再调用本机 OminiX-API/Qwen3-ASR。公网 VPS 只跑 Rust + Salvo 控制面，负责会话和识别文本转发，不承载音频或推理。

## 架构

```mermaid
flowchart LR
    B[Browser\nAgora Web SDK] -->|Opus / RTC audio| A[Agora SD-RTN]
    A -->|16 kHz PCM| G[LAN Bridge\nAgora Server SDK]
    G -->|HTTP / WAV| S[OminiX Qwen3-ASR\nprivate LAN]
    G -. outbound WSS .-> V[VPS\nRust + Salvo]
    V -. partial / final text .-> B
```

关键边界：OminiX-API 和内网 Bridge 都不需要公网入站端口；Bridge 主动连接 Agora 和 VPS。客户端确实需要集成 Agora RTC SDK，本 Demo 的浏览器实现位于 `control-plane/static/app.js`。

## 真实演示：唯一主流程

前置条件：OminiX-API/Qwen3-ASR 已在当前电脑的 `127.0.0.1:8080` 运行；已有 Agora App ID，以及同一频道中浏览器 UID `1001` 和 Bridge UID `9001` 的两个临时 RTC Token；VPS 子域已配置 HTTPS。

当前 Mac 启动 OminiX Qwen3-ASR：

```bash
cd /Users/alan0x/Documents/projects/agora-sensevoice-demo/bridge
bash start-ominix-asr.sh
```

保持该终端运行，再在另一个终端启动 Bridge。

1. Windows 构建并打包 VPS 控制面镜像，再上传 VPS。完整的可复制命令见 [`docs/WINDOWS_CODEX_HANDOFF.md`](docs/WINDOWS_CODEX_HANDOFF.md)。
2. VPS 创建 `deploy/.env`，使用真实配置：

   ```dotenv
   PUBLIC_BASE_URL=https://asr.pitun.cc
   ALLOWED_ORIGIN=https://asr.pitun.cc
   BRIDGE_SHARED_SECRET=<至少 16 位的随机密钥>
   DEMO_MODE=false
   AGORA_APP_ID=<APP_ID>
   DEMO_CHANNEL=sensevoice-demo
   DEMO_CLIENT_UID=1001
   DEMO_BRIDGE_UID=9001
   DEMO_CLIENT_RTC_TOKEN=<浏览器 TOKEN>
   DEMO_BRIDGE_RTC_TOKEN=<BRIDGE TOKEN>
   ```

3. VPS 只启动控制面：

   ```bash
   docker compose --env-file deploy/.env \
     -f deploy/docker-compose.yml up -d --no-build
   ```

4. 当前电脑启动真实 Bridge：

   ```bash
   cd bridge
   cp .env.example .env
   # 编辑 .env，只需替换 VPS 子域和共享密钥
   bash start-real.sh
   ```

   当前 Mac 的 `.venv` 已用 `/Users/alan0x/miniconda3/bin/python3.13` 创建并安装完整依赖，无需重装。`start-real.sh` 会先检查配置和 OminiX 健康状态，再启动真实 Bridge。

5. 打开 HTTPS 页面，点击“开始识别”，授权麦克风并讲话。页面显示的文字来自本机 OminiX Qwen3-ASR 实际推理。

在启动 Agora 前，可单独确认当前 OminiX ASR HTTP 链路：

```bash
cd bridge
python smoke_sensevoice_live.py /path/to/16k-mono.wav
```

本仓库已针对当前 OminiX-API 的 JSON + base64 协议实现并实际验证，不需要再改适配器。完整验收清单见 [`docs/REAL_DEMO_CHECKLIST.md`](docs/REAL_DEMO_CHECKLIST.md)。

如果在 Windows + Docker Desktop 上交叉构建 Linux 镜像、制作离线包并上传 VPS，请直接交给下一位 Codex 阅读 [`docs/WINDOWS_CODEX_HANDOFF.md`](docs/WINDOWS_CODEX_HANDOFF.md)。该文档明确保持“当前电脑运行 OminiX ASR/真实 Bridge，VPS 只运行控制面”的最终边界。

## 项目布局

```text
control-plane/  Rust + Salvo API、WebSocket 与静态演示页
bridge/         Agora Python Server SDK 接收器、断句器、ASR 适配器
deploy/         Docker Compose、环境变量与 Nginx 示例
docs/           协议与真实演示检查清单
```

## 当前非生产约束

- 单并发、内存会话、固定频道与固定 UID。
- RTC Token 由 Agora 控制台提前生成；不会把 App Certificate 放进服务或客户端。
- 文本通过 VPS WebSocket 回传，音频通过 Agora RTC；第一版不引入 RTM。
- Agora 官方将 macOS Server SDK 定位为开发/测试环境；本机适合本次 Demo，生产 Bridge 应迁到受支持的 Linux 主机。

这些限制只影响并发、凭据管理和可靠性，不会把音频或 ASR 替换成模拟数据；演示主流程中的 Agora 音频和 OminiX Qwen3-ASR 推理都是真实的。生产化下一步是接入官方 AccessToken2 builder 动态签发短期 Token，然后加入鉴权、限流、多会话路由、指标和断线恢复。

仓库仍保留 `mock-bridge`，仅供开发者在 Agora 或 OminiX 故障时定位控制面问题。它不是部署、验收或给老板演示的前置步骤。

## 本地检查

```bash
make check
```

要求：Rust 1.96+、Python 3.10+、Node.js。Docker 部署只需要 Docker Engine + Compose plugin。

## 参考

- [Agora Web SDK 文档](https://doc.shengwang.cn/doc/rtc/javascript/resources)
- [Agora Python Server SDK](https://github.com/AgoraIO-Extensions/Agora-Python-Server-SDK)
- [Salvo WebSocket API](https://docs.rs/salvo/latest/salvo/websocket/)

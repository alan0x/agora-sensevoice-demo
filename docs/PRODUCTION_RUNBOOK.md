# Production foundation runbook

本文用于把已验收 Demo 迁移到第一阶段生产基线。当前设计是受控单并发服务：真实 Agora 音频、真实 OminiX 推理、动态短期凭据；不把单 worker 包装成多并发。

## 1. 变更与回滚基线

- 已验收 Demo：Git 标签 `demo-approved-2026-09-01`。
- 生产化开发分支：`codex/production-foundation`。
- 升级前保留当前 VPS 的 `deploy/.env` 与旧 `agora-sensevoice-control-plane:demo` 镜像；二者都不要提交 Git。

若新版本验收失败，可重新部署该标签对应的旧包和旧环境变量。旧版依赖两个手工 RTC Token，新版依赖 App Certificate，不能混用 `.env`。

## 2. VPS 密钥与配置

在 VPS 上生成两个互不相同的密钥：

```bash
openssl rand -hex 32
openssl rand -hex 32
```

第一个填 `BRIDGE_SHARED_SECRET`，同步到 Mac 的 `bridge/.env`；第二个填 `CLIENT_ACCESS_TOKEN`，只发给获准使用网页的人。创建 `deploy/.env`：

```dotenv
PUBLIC_BASE_URL=https://asr.pitun.cc
ALLOWED_ORIGIN=https://asr.pitun.cc
CONTROL_PLANE_PORT=18080
BRIDGE_SHARED_SECRET=<64 hex chars>
CLIENT_ACCESS_TOKEN=<different 64 hex chars>
SESSION_TTL_SECONDS=900
DEMO_MODE=false
AGORA_APP_ID=<32-char App ID>
AGORA_APP_CERTIFICATE=<32-char App Certificate>
RTC_CHANNEL_PREFIX=asr
RTC_CLIENT_UID=1001
RTC_BRIDGE_UID=9001
RTC_TOKEN_TTL_SECONDS=1200
```

写入后执行 `chmod 600 deploy/.env`。`BRIDGE_SHARED_SECRET` 与 `CLIENT_ACCESS_TOKEN` 不能相同。

`RTC_TOKEN_TTL_SECONDS` 必须至少比会话 TTL 多 60 秒，且不超过 86400。App Certificate 只能存在于 VPS secret/env 管理中，不能出现在浏览器、Mac Bridge、镜像或仓库。

## 3. 构建和部署

Windows 离线构建/上传命令见 [`WINDOWS_CODEX_HANDOFF.md`](WINDOWS_CODEX_HANDOFF.md)。VPS 已加载镜像与代码包后：

```bash
docker network inspect shared_network >/dev/null 2>&1 || docker network create shared_network
docker compose --env-file deploy/.env -f deploy/docker-compose.yml up -d --no-build
docker compose --env-file deploy/.env -f deploy/docker-compose.yml ps
docker compose --env-file deploy/.env -f deploy/docker-compose.yml logs --tail=100
```

把 `deploy/nginx.conf.example` 合并到现有 Nginx `http {}`，保留 WebSocket Upgrade、TLS 1.2/1.3、HSTS 和每 IP 限流；然后 `nginx -t` 成功后 reload。

## 4. VPS 验证

Bridge 未启动前：

```bash
curl -i https://asr.pitun.cc/healthz
curl -i https://asr.pitun.cc/readyz
curl -sS https://asr.pitun.cc/api/v1/status
curl -i -X POST https://asr.pitun.cc/api/v1/sessions
```

期望 `/healthz` 为 `200 ok`，`/readyz` 为 `503`，status 中 `bridgeOnline=false`、`accessProtected=true`、`capacity=1`；未带访问密钥创建会话必须返回 `401`。不要在命令行历史里直接展开真实访问密钥，端到端创建会话用浏览器验证。

## 5. Mac 前台运行

当前选择由操作员在两个终端中前台运行，不安装 LaunchAgent。终端一启动 OminiX：

```bash
cd /Users/alan0x/Documents/projects/agora-sensevoice-demo/bridge
bash start-ominix-asr.sh
```

确认 `http://127.0.0.1:8080/health` 正常后，终端二启动 Bridge：

```bash
cd /Users/alan0x/Documents/projects/agora-sensevoice-demo/bridge
bash start-real.sh
```

两个终端都需要保持运行，停止时分别按 `Ctrl-C`。确认 `bridge/.env` 的 `BRIDGE_SHARED_SECRET` 与 VPS 一致；`start-real.sh` 会等待 OminiX 健康后再连接公网控制面。

## 6. 端到端验收

1. `/readyz` 变为 `200 ready`，status 显示 `bridgeOnline=true`。
2. 打开 HTTPS 页面，输入 `CLIENT_ACCESS_TOKEN`；密钥仅保存在当前标签页。
3. 点击“开始识别”并允许麦克风；页面网络响应中的频道应为 `asr-<随机值>`，Token 以 `007` 开头。
4. 说一条临时中文句子，页面出现真实 OminiX final 文本。
5. 在“识别链路观测”确认该句包含 Bridge 断句、OminiX HTTP、VPS 转发和文字 ACK 数据；连续采集至少 30 条后下载 JSON。
6. 验证静音、立即断句、结束会话。
7. 再开一个会话时频道值必须变化；活动会话期间第二个创建请求应返回 `409`。
8. 检查浏览器、Nginx 日志与 Git：不得出现 App Certificate、Bridge secret 或 WebSocket ticket。

生成 Markdown 延时汇报：

```bash
cd /Users/alan0x/Documents/projects/agora-sensevoice-demo/bridge
python summarize_trace.py ~/Downloads/agora-asr-trace-*.json \
  --output ~/Downloads/agora-asr-latency-report.md
```

至少汇报有效样本数、端到端链路估算 P50/P95、“立即断句→最终文本”实测 P50/P95、首个 partial P50/P95、Bridge 断句、OminiX HTTP 往返、RTF、丢包率和文字交付 ACK RTT。客户端音量阈值估算只作诊断，不作为主延时指标。页面导出包含识别文本，按业务语音数据处理，不要提交到 Git。

## 7. 当前容量与下一阶段

本阶段仍有三条已知边界：会话状态只在单控制面进程内存中；页面使用共享 operator access token 而不是个人身份；Mac 上的 Agora Server SDK 属于当前落地约束。下一里程碑应按顺序完成 OIDC/SSO、Redis 会话/审计、多个 OminiX worker 调度、Prometheus/告警，再评估 Bridge 迁移到受支持 Linux 环境。

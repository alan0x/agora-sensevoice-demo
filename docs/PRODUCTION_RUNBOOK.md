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

## 5. Mac 守护运行

确认 `bridge/.env` 的 `BRIDGE_SHARED_SECRET` 与 VPS 一致，并先前台启动 OminiX、Bridge 各验证一次；验证完成后用 Ctrl-C 停掉两个前台进程。随后：

```bash
cd /Users/alan0x/Documents/projects/agora-sensevoice-demo
bash deploy/macos/install-launch-agents.sh
bash deploy/macos/status.sh
```

LaunchAgent 日志位于 `~/Library/Logs/agora-ominix-asr/`。查看状态和日志：

```bash
launchctl print gui/$UID/com.pitun.ominix-asr
launchctl print gui/$UID/com.pitun.agora-bridge
tail -f ~/Library/Logs/agora-ominix-asr/*.log
```

安装脚本只创建当前用户的两个 LaunchAgent，不需要 root。Bridge 会等待 OminiX 健康后启动，两个进程异常退出都会自动拉起。

## 6. 端到端验收

1. `/readyz` 变为 `200 ready`，status 显示 `bridgeOnline=true`。
2. 打开 HTTPS 页面，输入 `CLIENT_ACCESS_TOKEN`；密钥仅保存在当前标签页。
3. 点击“开始识别”并允许麦克风；页面网络响应中的频道应为 `asr-<随机值>`，Token 以 `007` 开头。
4. 说一条临时中文句子，页面出现真实 OminiX final 文本。
5. 验证静音、立即断句、结束会话。
6. 再开一个会话时频道值必须变化；活动会话期间第二个创建请求应返回 `409`。
7. 检查浏览器、Nginx 日志与 Git：不得出现 App Certificate、Bridge secret 或 WebSocket ticket。

## 7. 当前容量与下一阶段

本阶段仍有三条已知边界：会话状态只在单控制面进程内存中；页面使用共享 operator access token 而不是个人身份；Mac 上的 Agora Server SDK 属于当前落地约束。下一里程碑应按顺序完成 OIDC/SSO、Redis 会话/审计、多个 OminiX worker 调度、Prometheus/告警，再评估 Bridge 迁移到受支持 Linux 环境。

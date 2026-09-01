# Windows Codex 交接：构建、打包并上传 VPS 镜像

> 本文是给 Windows 机器上的下一位 Codex/开发者使用的执行上下文。开始工作前，请先读完本文和仓库根目录 `README.md`。

## 1. 最终部署边界（不要改变）

最终真实链路固定如下：

```text
Browser + Agora Web SDK
          │ RTC audio
          ▼
      Agora SD-RTN
          │ 16 kHz PCM
          ▼
当前电脑：Agora Bridge → 本机 SenseVoice
          │ outbound WSS / ASR text
          ▼
VPS：Rust + Salvo control-plane → Browser
```

- **当前电脑**：继续运行真实 `bridge` 和 SenseVoice。
- **Windows 电脑**：只用于构建 Linux Docker 镜像、制作离线包和上传。
- **VPS**：只运行 `control-plane`。如果需要先展示 mock Demo，可临时额外运行 `mock-bridge`；真实演示时不要在 VPS 运行真实 Bridge 或 SenseVoice。
- 浏览器必须通过 HTTPS 打开页面，麦克风音频只走 Agora RTC；VPS 不承载音频和推理。

## 2. 当前仓库状态

- GitHub：`https://github.com/alan0x/agora-sensevoice-demo`（private）
- 默认分支：`main`
- 已完成：Salvo 控制面、Agora Web SDK 页面、mock/real Bridge、SenseVoice HTTP adapter、Dockerfile、Compose、Nginx 示例、单元测试和 CI。
- 已验证：本地 mock 端到端事件链路 `session.snapshot → asr.partial → asr.final`。
- VPS Compose 使用的镜像名：
  - `agora-sensevoice-control-plane:demo`
  - mock 可选：`agora-sensevoice-mock-bridge:demo`

## 3. Windows 前置检查

使用 Docker Desktop 的 WSL 2 后端和 **Linux containers**。在 PowerShell 中执行：

```powershell
wsl --version
docker version
docker compose version
docker buildx version
gh auth status
```

Clone private 仓库：

```powershell
gh repo clone alan0x/agora-sensevoice-demo
Set-Location agora-sensevoice-demo
git status --short --branch
```

不要在 OneDrive 同步目录中构建；建议使用 `C:\src\agora-sensevoice-demo` 或 WSL Linux 文件系统。

## 4. 构建前先确认 VPS CPU 架构

Windows 上执行：

```powershell
ssh <VPS_USER>@<VPS_HOST> "uname -m"
```

映射关系：

| `uname -m` | Docker 平台 | 包名后缀 |
|---|---|---|
| `x86_64` | `linux/amd64` | `linux-amd64` |
| `aarch64` / `arm64` | `linux/arm64` | `linux-arm64` |

以下命令默认 VPS 是常见的 `x86_64`。如果实际为 ARM，必须同时替换 `$Platform` 和 `$ArchLabel`：

```powershell
$Platform = "linux/amd64"
$ArchLabel = "linux-amd64"
$ControlImage = "agora-sensevoice-control-plane:demo"
$MockImage = "agora-sensevoice-mock-bridge:demo"
New-Item -ItemType Directory -Force artifacts | Out-Null
docker buildx inspect --bootstrap
```

## 5. 构建 VPS 控制面镜像

```powershell
docker buildx build `
  --platform $Platform `
  --file control-plane/Dockerfile `
  --tag $ControlImage `
  --load `
  control-plane
```

确认目标平台，不要只看构建命令是否退出 0：

```powershell
docker image inspect $ControlImage --format '{{.Os}}/{{.Architecture}}'
```

确认输出与 `$Platform` 一致。完整启动和健康检查使用第 7 节的 mock 验证。

## 6. 可选：同时构建 VPS mock Bridge

只有需要在 VPS 先展示“无 Agora/SenseVoice 凭据”的 mock Demo 时才构建：

```powershell
docker buildx build `
  --platform $Platform `
  --file bridge/Dockerfile.mock `
  --tag $MockImage `
  --load `
  bridge
```

真实演示时，VPS 不使用该镜像；真实 Bridge 始终留在当前电脑。

## 7. Windows 上先跑一次 mock 验证

```powershell
Copy-Item deploy\.env.example deploy\.env

$bytes = [byte[]]::new(24)
[System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
$secret = [BitConverter]::ToString($bytes).Replace("-", "").ToLowerInvariant()
$secret
notepad deploy\.env
```

在 `deploy/.env` 中填写生成的密钥，并保持：

```dotenv
DEMO_MODE=true
ALLOWED_ORIGIN=
CONTROL_PLANE_PORT=18080
```

使用刚构建的本地镜像启动，特意加 `--no-build`，避免 Compose 又构建一遍：

```powershell
docker compose --env-file deploy/.env `
  -f deploy/docker-compose.yml `
  --profile mock up -d --no-build

docker compose --env-file deploy/.env `
  -f deploy/docker-compose.yml `
  --profile mock ps

Invoke-RestMethod http://localhost:18080/healthz
Invoke-RestMethod http://localhost:18080/api/v1/status
Start-Process http://localhost:18080
```

预期 `bridgeOnline=true`、`demoMode=true`，网页点击“开始识别”后出现 partial/final 模拟文本。

如失败，收集：

```powershell
docker compose --env-file deploy/.env `
  -f deploy/docker-compose.yml `
  --profile mock logs --tail=200
```

验证结束：

```powershell
docker compose --env-file deploy/.env `
  -f deploy/docker-compose.yml `
  --profile mock down
```

## 8. 生成离线镜像包和部署源码包

真实部署只打包控制面：

```powershell
$ControlTar = "artifacts\agora-sensevoice-control-plane-$ArchLabel.tar"
docker save --output $ControlTar $ControlImage
git archive --format=zip --output="artifacts\agora-sensevoice-demo-deploy.zip" HEAD
```

如果 VPS 还要跑 mock，把两个镜像放进同一个 tar：

```powershell
$MockTar = "artifacts\agora-sensevoice-vps-mock-$ArchLabel.tar"
docker save --output $MockTar $ControlImage $MockImage
```

生成 SHA-256：

```powershell
Get-FileHash artifacts\*.tar, artifacts\*.zip -Algorithm SHA256 |
  Format-Table Algorithm, Hash, Path -AutoSize |
  Out-File artifacts\SHA256SUMS.txt -Encoding utf8

Get-Content artifacts\SHA256SUMS.txt
```

不要把 `deploy/.env`、Agora App Certificate、RTC Token 或 Bridge secret 放进 zip/tar；`.env` 已被 Git 忽略。

## 9. 上传到 VPS

先创建普通用户可写目录：

```powershell
ssh <VPS_USER>@<VPS_HOST> "mkdir -p ~/agora-sensevoice-upload"
scp artifacts\agora-sensevoice-control-plane-$ArchLabel.tar `
  <VPS_USER>@<VPS_HOST>:~/agora-sensevoice-upload/
scp artifacts\agora-sensevoice-demo-deploy.zip `
  <VPS_USER>@<VPS_HOST>:~/agora-sensevoice-upload/
scp artifacts\SHA256SUMS.txt `
  <VPS_USER>@<VPS_HOST>:~/agora-sensevoice-upload/
```

如部署 mock，则上传 `$MockTar`，而不是只含控制面的 tar。

## 10. VPS 加载并启动（不在 VPS 构建）

SSH 登录 VPS：

```bash
cd ~/agora-sensevoice-upload
sha256sum *.tar *.zip
docker load --input agora-sensevoice-control-plane-linux-amd64.tar
unzip -q agora-sensevoice-demo-deploy.zip -d app
cd app
cp deploy/.env.example deploy/.env
```

如果 VPS 是 ARM，tar 文件名相应改成 `linux-arm64`。

编辑 `deploy/.env`：

```dotenv
PUBLIC_BASE_URL=https://<ASR_SUBDOMAIN>
ALLOWED_ORIGIN=https://<ASR_SUBDOMAIN>
BRIDGE_SHARED_SECRET=<RANDOM_SECRET_SHARED_WITH_CURRENT_COMPUTER>
DEMO_MODE=false
AGORA_APP_ID=<APP_ID>
DEMO_CHANNEL=sensevoice-demo
DEMO_CLIENT_UID=1001
DEMO_BRIDGE_UID=9001
DEMO_CLIENT_RTC_TOKEN=<TEMP_BROWSER_TOKEN>
DEMO_BRIDGE_RTC_TOKEN=<TEMP_BRIDGE_TOKEN>
```

真实模式只启动控制面，并强制禁止 VPS 构建：

```bash
docker compose --env-file deploy/.env \
  -f deploy/docker-compose.yml \
  up -d --no-build
docker compose --env-file deploy/.env \
  -f deploy/docker-compose.yml ps
curl http://127.0.0.1:18080/healthz
curl http://127.0.0.1:18080/api/v1/status
```

`bridgeOnline=false` 在当前电脑的真实 Bridge 尚未启动时是正常的。

如先跑 VPS mock：先 `docker load` 包含两个镜像的 tar，将 `DEMO_MODE=true`，再执行：

```bash
docker compose --env-file deploy/.env \
  -f deploy/docker-compose.yml \
  --profile mock up -d --no-build
```

## 11. 当前电脑启动真实 Bridge

这一步不在 Windows、不在 VPS 执行。回到运行 SenseVoice 的当前电脑：

```bash
cd /Users/alan0x/Documents/projects/agora-sensevoice-demo/bridge
python3.10 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt

export CONTROL_WS_URL=wss://<ASR_SUBDOMAIN>/ws/bridge
export BRIDGE_SHARED_SECRET='<SAME_AS_VPS>'
export SENSEVOICE_URL='http://127.0.0.1:8000/v1/audio/transcriptions'
export SENSEVOICE_MODEL='SenseVoiceSmall'
python -m bridge.main --mode real
```

若现有 SenseVoice 不是 OpenAI-compatible multipart 接口，只修改 `bridge/bridge/sensevoice.py`；不要让 VPS 直接访问内网 SenseVoice。

## 12. HTTPS/Nginx 与最终验收

VPS 的 Nginx 参考 `deploy/nginx.conf.example`，必须保留 WebSocket Upgrade headers，并将 HTTPS 子域反代到 `127.0.0.1:18080`。

验收顺序：

1. `https://<ASR_SUBDOMAIN>/healthz` 返回 `ok`。
2. `/api/v1/status` 显示 `bridgeOnline=true`、`demoMode=false`。
3. 浏览器点击“开始识别”并允许麦克风。
4. 浏览器 Agora 状态变为在线；当前电脑 Bridge 日志出现远端 UID。
5. 讲话后页面出现 SenseVoice partial/final 文本。
6. 测试“立即断句”、静音和结束会话。

## 13. 常见问题定位

| 现象 | 优先检查 |
|---|---|
| VPS 报 `exec format error` | Windows 构建平台与 VPS `uname -m` 不匹配 |
| `bridgeOnline=false` | 当前电脑 Bridge 未运行、secret 不一致、WSS/Nginx Upgrade 配置错误 |
| 浏览器不能获取麦克风 | 页面不是 HTTPS，或浏览器权限被拒绝 |
| Agora join 失败 | Token 过期，Token 的 channel/UID 与配置不一致 |
| Bridge 在线但无文本 | 当前电脑能否访问 SenseVoice；`SENSEVOICE_URL` 和响应 schema 是否匹配 |
| VPS 试图重新编译 | 启动命令遗漏了 `--no-build`，或加载后的镜像 tag 不匹配 |

## 14. 给下一位 Codex 的明确要求

1. 先通过 SSH 获取 VPS `uname -m`，不要猜架构。
2. Windows 只负责构建、验证、打包、校验和上传；不要把 SenseVoice/真实 Bridge 迁到 Windows 或 VPS。
3. VPS 必须使用 `docker load` 后的镜像和 `--no-build` 启动。
4. 不要把任何 `.env`、App Certificate、RTC Token、secret 提交到 Git。
5. 真实 Bridge 的 `BRIDGE_SHARED_SECRET` 必须和 VPS 完全相同。
6. 任何失败都先保存 `docker compose ps`、`docker compose logs --tail=200`、镜像 inspect 和 VPS 架构输出，再修改代码。
7. 若修改仓库，运行测试、提交到新分支或明确提交，并把变更推送回 GitHub，保证当前电脑可以同步。

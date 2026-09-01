# Windows Codex 交接：构建真实 Demo 的 VPS 镜像并上传

> 给 Windows 机器上的下一位 Codex/开发者：先完整阅读本文和仓库根目录 `README.md`。不要把 mock 当成构建、验收或演示步骤。

## 1. 固定部署边界

```text
Browser + Agora Web SDK
          │ real RTC audio
          ▼
      Agora SD-RTN
          │ 16 kHz mono PCM
          ▼
当前 Mac：Agora Bridge → 本机 SenseVoice :8094
          │ outbound WSS / real ASR text
          ▼
VPS：Rust + Salvo control-plane → Browser
```

- 当前 Mac：运行真实 Bridge 和 SenseVoice。
- Windows：只构建 Linux 控制面镜像、制作离线包、上传 VPS。
- VPS：只运行 Rust + Salvo `control-plane`。
- 浏览器必须使用 HTTPS；麦克风音频只走 Agora，VPS 不承载音频或推理。
- 仓库里的 `mock-bridge` 仅用于开发者隔离控制面故障，默认跳过，不能作为最终验收。

## 2. 已验证状态

- GitHub：`https://github.com/alan0x/agora-sensevoice-demo`（private），默认分支 `main`。
- 当前 Mac 是 Apple Silicon，Python 3.13 已成功加载 `agora-python-server-sdk==2.4.9`。
- Bridge 已按当前 SenseVoice 的 JSON + base64 协议适配。
- `127.0.0.1:8094` 的真实 SenseVoice 已用 16 kHz 单声道 WAV 验证，返回“你好，小章鱼。”。
- VPS 唯一需要的镜像：`agora-sensevoice-control-plane:demo`。

## 3. Windows 前置检查

Docker Desktop 使用 WSL 2 后端和 Linux containers。在 PowerShell 中执行：

```powershell
wsl --version
docker version
docker compose version
docker buildx version
gh auth status
```

Clone 仓库；建议放在 `C:\src` 或 WSL 文件系统，不要放 OneDrive 同步目录：

```powershell
Set-Location C:\src
gh repo clone alan0x/agora-sensevoice-demo
Set-Location agora-sensevoice-demo
git status --short --branch
```

## 4. 查 VPS 架构并设置变量

```powershell
ssh <VPS_USER>@<VPS_HOST> "uname -m"
```

| `uname -m` | `$Platform` | `$ArchLabel` |
|---|---|---|
| `x86_64` | `linux/amd64` | `linux-amd64` |
| `aarch64` / `arm64` | `linux/arm64` | `linux-arm64` |

下面默认 VPS 是 `x86_64`；若输出不同，先改变量：

```powershell
$Platform = "linux/amd64"
$ArchLabel = "linux-amd64"
$ControlImage = "agora-sensevoice-control-plane:demo"
$ControlTar = "artifacts\agora-sensevoice-control-plane-$ArchLabel.tar"
New-Item -ItemType Directory -Force artifacts | Out-Null
docker buildx inspect --bootstrap
```

## 5. 构建并检查真实控制面镜像

```powershell
docker buildx build `
  --platform $Platform `
  --file control-plane/Dockerfile `
  --tag $ControlImage `
  --load `
  control-plane

docker image inspect $ControlImage --format '{{.Os}}/{{.Architecture}}'
```

最后一行必须与 `$Platform` 相符。这里只检查镜像能构建并匹配 VPS 架构；不需要启动 mock。

## 6. 打包镜像和部署文件

```powershell
docker save --output $ControlTar $ControlImage
git archive --format=zip `
  --output="artifacts\agora-sensevoice-demo-deploy.zip" `
  HEAD

Get-FileHash $ControlTar, artifacts\agora-sensevoice-demo-deploy.zip `
  -Algorithm SHA256 | Format-Table Algorithm, Hash, Path -AutoSize
```

记录终端中的两个 SHA-256，上传后核对。不要把 `deploy/.env`、App Certificate、RTC Token 或 Bridge secret 放进包里。

## 7. 上传 VPS

```powershell
ssh <VPS_USER>@<VPS_HOST> "mkdir -p ~/agora-sensevoice-upload"

scp $ControlTar `
  <VPS_USER>@<VPS_HOST>:~/agora-sensevoice-upload/

scp artifacts\agora-sensevoice-demo-deploy.zip `
  <VPS_USER>@<VPS_HOST>:~/agora-sensevoice-upload/
```

## 8. VPS 加载并启动控制面

SSH 登录 VPS：

```bash
cd ~/agora-sensevoice-upload
sha256sum *.tar *.zip
docker load --input agora-sensevoice-control-plane-linux-amd64.tar
unzip -q agora-sensevoice-demo-deploy.zip -d app
cd app
cp deploy/.env.example deploy/.env
```

ARM VPS 将 tar 文件名改为 `linux-arm64`。将 `deploy/.env` 填成真实值：

```dotenv
PUBLIC_BASE_URL=https://<ASR_SUBDOMAIN>
ALLOWED_ORIGIN=https://<ASR_SUBDOMAIN>
CONTROL_PLANE_PORT=18080
BRIDGE_SHARED_SECRET=<RANDOM_SECRET_SHARED_WITH_CURRENT_MAC>
SESSION_TTL_SECONDS=900
DEMO_MODE=false
AGORA_APP_ID=<APP_ID>
DEMO_CHANNEL=sensevoice-demo
DEMO_CLIENT_UID=1001
DEMO_BRIDGE_UID=9001
DEMO_CLIENT_RTC_TOKEN=<TEMP_BROWSER_TOKEN>
DEMO_BRIDGE_RTC_TOKEN=<TEMP_BRIDGE_TOKEN>
```

两个 Token 必须属于相同 App ID、相同频道 `sensevoice-demo`，并分别绑定 UID `1001`、`9001`。不要把 App Certificate 放到 VPS 或浏览器。

启动已经加载的镜像，明确禁止 VPS 重新构建：

```bash
docker compose --env-file deploy/.env \
  -f deploy/docker-compose.yml \
  up -d --no-build

docker compose --env-file deploy/.env \
  -f deploy/docker-compose.yml ps

curl http://127.0.0.1:18080/healthz
curl http://127.0.0.1:18080/api/v1/status
```

真实 Bridge 尚未启动时 `bridgeOnline=false` 是正常的。

## 9. VPS 配 HTTPS

参考 `deploy/nginx.conf.example`，把 HTTPS 子域反代至 `127.0.0.1:18080`，保留 WebSocket Upgrade headers。检查：

```bash
curl https://<ASR_SUBDOMAIN>/healthz
```

浏览器麦克风要求安全上下文，所以最终页面必须使用 HTTPS，不能用 VPS IP 的 HTTP 页面代替。

## 10. 回到当前 Mac 启动真实 Bridge

以下命令不在 Windows 或 VPS 执行：

```bash
cd /Users/alan0x/Documents/projects/agora-sensevoice-demo/bridge
cp .env.example .env
# 编辑 .env：替换 CONTROL_WS_URL 和 BRIDGE_SHARED_SECRET
bash start-real.sh
```

`.venv` 已在当前 Mac 上创建并安装完整依赖。若以后重建：

```bash
/Users/alan0x/miniconda3/bin/python3.13 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

需要单独确认 SenseVoice 时，使用真实音频，而不是 mock：

```bash
python smoke_sensevoice_live.py /path/to/16k-mono.wav
```

## 11. 最终验收（全部必须通过）

1. `https://<ASR_SUBDOMAIN>/healthz` 返回 `ok`。
2. `/api/v1/status` 显示 `bridgeOnline=true`、`demoMode=false`。
3. 浏览器页面显示 `REAL 真实链路`，点击“开始识别”并允许麦克风。
4. 当前 Mac Bridge 日志显示 Agora 已连接和浏览器 UID 已入会。
5. 对麦克风说一句临时内容，页面出现与该内容一致的 SenseVoice final 文本。
6. 再测试“立即断句”、静音/恢复和结束会话。
7. 浏览器网络请求中没有任何内网 SenseVoice 地址。

预设模拟文本、`demoMode=true` 或 mock Bridge 在线都不算通过。

## 12. 故障定位

| 现象 | 优先检查 |
|---|---|
| VPS `exec format error` | 镜像平台与 `uname -m` 不一致 |
| `bridgeOnline=false` | Mac Bridge 是否运行、secret 是否一致、Nginx WSS Upgrade |
| 浏览器无麦克风 | 是否 HTTPS、浏览器权限是否允许 |
| Agora join 失败 | Token 是否过期；channel、UID、App ID 是否严格一致 |
| Bridge 在线但无文本 | `curl 127.0.0.1:8094/health`；Bridge 日志；SenseVoice URL/protocol |
| VPS 尝试编译 | 是否遗漏 `--no-build`；镜像 tag 是否为 `agora-sensevoice-control-plane:demo` |

若失败，先保存以下输出再改代码：

```bash
docker compose --env-file deploy/.env -f deploy/docker-compose.yml ps
docker compose --env-file deploy/.env -f deploy/docker-compose.yml logs --tail=200
docker image inspect agora-sensevoice-control-plane:demo
```

## 13. 给下一位 Codex 的提交要求

1. 不改变“Mac 跑 Bridge + SenseVoice、VPS 只跑控制面”的边界。
2. 不要求用户先构建、运行或验收 mock。
3. 不提交 `.env`、App Certificate、RTC Token 或 secret。
4. 修改仓库后运行 `make check`，提交并推送 GitHub，保证当前 Mac 可同步。

const ui = {
  start: document.querySelector("#startBtn"),
  mute: document.querySelector("#muteBtn"),
  commit: document.querySelector("#commitBtn"),
  stop: document.querySelector("#stopBtn"),
  transcript: document.querySelector("#transcript"),
  partial: document.querySelector("#partial"),
  eventLog: document.querySelector("#eventLog"),
  sessionId: document.querySelector("#sessionId"),
  sessionState: document.querySelector("#sessionState"),
  bridgeState: document.querySelector("#bridgeState"),
  modeBadge: document.querySelector("#modeBadge"),
  browserDot: document.querySelector("#browserDot"),
  agoraDot: document.querySelector("#agoraDot"),
  bridgeDot: document.querySelector("#bridgeDot"),
  asrDot: document.querySelector("#asrDot"),
  level: document.querySelector(".audio-level"),
  levelBars: [...document.querySelectorAll(".audio-level i")],
};

const runtime = {
  session: null,
  socket: null,
  rtcClient: null,
  microphone: null,
  muted: false,
  meterTimer: null,
};

function log(message, data) {
  const stamp = new Date().toLocaleTimeString();
  const suffix = data === undefined ? "" : ` ${JSON.stringify(data)}`;
  ui.eventLog.textContent = `[${stamp}] ${message}${suffix}\n${ui.eventLog.textContent}`.slice(0, 8000);
}

function setDot(element, state) {
  element.className = `status-dot${state ? ` ${state}` : ""}`;
}

function setSessionState(label) {
  ui.sessionState.textContent = label;
}

function setRunning(running) {
  ui.start.disabled = running;
  ui.mute.disabled = !running;
  ui.commit.disabled = !running;
  ui.stop.disabled = !running;
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.error?.message || `${response.status} ${response.statusText}`);
  }
  return response.status === 204 ? null : response.json();
}

async function refreshStatus() {
  try {
    const status = await api("/api/v1/status");
    ui.bridgeState.textContent = status.bridgeOnline ? "在线" : "离线";
    ui.modeBadge.textContent = status.demoMode ? "MOCK 演示模式" : "REAL 真实链路";
    setDot(ui.browserDot, "on");
    setDot(ui.bridgeDot, status.bridgeOnline ? "on" : "");
  } catch (error) {
    setDot(ui.browserDot, "");
    log("控制面状态检查失败", { error: error.message });
  }
}

function openEventSocket(path) {
  const scheme = location.protocol === "https:" ? "wss:" : "ws:";
  const socket = new WebSocket(`${scheme}//${location.host}${path}`);
  runtime.socket = socket;
  socket.addEventListener("open", () => log("文本事件 WebSocket 已连接"));
  socket.addEventListener("message", (event) => {
    const payload = JSON.parse(event.data);
    log(`收到 ${payload.type}`, payload.text ? { text: payload.text } : undefined);
    handleEvent(payload);
  });
  socket.addEventListener("close", () => log("文本事件 WebSocket 已断开"));
  socket.addEventListener("error", () => log("文本事件 WebSocket 异常"));
}

function handleEvent(event) {
  if (event.type === "session.snapshot") {
    if (event.state === "ready") markReady();
    return;
  }
  if (event.type === "session.ready") {
    markReady();
    return;
  }
  if (event.type === "asr.partial") {
    ui.partial.textContent = event.text || "";
    setDot(ui.asrDot, "busy");
    return;
  }
  if (event.type === "asr.final") {
    ui.partial.textContent = "";
    appendFinal(event.text || "");
    setDot(ui.asrDot, "on");
    return;
  }
  if (event.type === "asr.error") {
    setSessionState("识别错误");
    setDot(ui.asrDot, "");
    appendFinal(`⚠ ${event.message || "ASR 发生错误"}`);
    return;
  }
  if (event.type === "session.closed") {
    cleanupLocal();
  }
}

function markReady() {
  setSessionState("识别中");
  setDot(ui.bridgeDot, "on");
  setDot(ui.asrDot, "on");
}

function appendFinal(text) {
  if (!text) return;
  ui.transcript.querySelector(".placeholder")?.remove();
  const line = document.createElement("p");
  line.className = "final";
  line.textContent = text;
  ui.transcript.append(line);
  line.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

async function joinAgora(config) {
  if (!window.AgoraRTC) throw new Error("Agora Web SDK 加载失败");
  window.AgoraRTC.setLogLevel(2);
  const client = window.AgoraRTC.createClient({ mode: "live", codec: "vp8" });
  await client.setClientRole("host");
  await client.join(config.appId, config.channel, config.token, config.uid);
  const microphone = await window.AgoraRTC.createMicrophoneAudioTrack({
    encoderConfig: "speech_standard",
    AEC: true,
    ANS: true,
    AGC: true,
  });
  await client.publish([microphone]);
  runtime.rtcClient = client;
  runtime.microphone = microphone;
  setDot(ui.agoraDot, "on");
  startMeter();
  log("已加入 Agora RTC 并发布麦克风", { channel: config.channel, uid: config.uid });
}

function startMeter() {
  clearInterval(runtime.meterTimer);
  ui.level.classList.add("active");
  runtime.meterTimer = setInterval(() => {
    const volume = runtime.microphone?.getVolumeLevel?.() || 0;
    ui.levelBars.forEach((bar, index) => {
      const threshold = index / ui.levelBars.length;
      bar.style.height = `${5 + (volume > threshold ? 17 * Math.min(1, volume + 0.25) : 0)}px`;
    });
  }, 100);
}

async function start() {
  ui.start.disabled = true;
  setSessionState("正在启动");
  setDot(ui.agoraDot, "busy");
  try {
    const session = await api("/api/v1/sessions", { method: "POST", body: "{}" });
    runtime.session = session;
    ui.sessionId.textContent = session.sessionId.slice(0, 8);
    ui.sessionId.title = session.sessionId;
    openEventSocket(session.eventsWsPath);
    if (session.demoMode) {
      setDot(ui.agoraDot, "on");
      log("MOCK 模式：跳过浏览器麦克风与 Agora 入会");
    } else {
      await joinAgora(session.agora);
    }
    setRunning(true);
  } catch (error) {
    log("启动失败", { error: error.message });
    appendFinal(`⚠ 启动失败：${error.message}`);
    setSessionState("启动失败");
    setDot(ui.agoraDot, "");
    if (runtime.session) await stop();
    else setRunning(false);
  }
}

async function toggleMute() {
  runtime.muted = !runtime.muted;
  if (runtime.microphone) await runtime.microphone.setEnabled(!runtime.muted);
  ui.mute.textContent = runtime.muted ? "取消静音" : "静音";
  ui.level.classList.toggle("active", !runtime.muted);
  log(runtime.muted ? "麦克风已静音" : "麦克风已恢复");
}

async function commit() {
  if (!runtime.session) return;
  try {
    await api(`/api/v1/sessions/${runtime.session.sessionId}/commit`, {
      method: "POST",
      body: "{}",
    });
    log("已请求立即断句");
  } catch (error) {
    log("断句请求失败", { error: error.message });
  }
}

async function stop() {
  const session = runtime.session;
  if (session) {
    try {
      await api(`/api/v1/sessions/${session.sessionId}`, { method: "DELETE" });
    } catch (error) {
      log("结束会话请求失败", { error: error.message });
    }
  }
  await cleanupLocal();
}

async function cleanupLocal() {
  clearInterval(runtime.meterTimer);
  runtime.meterTimer = null;
  runtime.microphone?.stop();
  runtime.microphone?.close();
  if (runtime.rtcClient) await runtime.rtcClient.leave().catch(() => {});
  runtime.socket?.close();
  runtime.session = null;
  runtime.socket = null;
  runtime.rtcClient = null;
  runtime.microphone = null;
  runtime.muted = false;
  ui.mute.textContent = "静音";
  ui.level.classList.remove("active");
  ui.levelBars.forEach((bar) => { bar.style.height = "5px"; });
  ui.partial.textContent = "";
  ui.sessionId.textContent = "—";
  setSessionState("待机");
  setDot(ui.agoraDot, "");
  setDot(ui.asrDot, "");
  setRunning(false);
  refreshStatus();
}

ui.start.addEventListener("click", start);
ui.mute.addEventListener("click", toggleMute);
ui.commit.addEventListener("click", commit);
ui.stop.addEventListener("click", stop);
window.addEventListener("beforeunload", () => runtime.microphone?.close());

refreshStatus();
setInterval(refreshStatus, 5000);

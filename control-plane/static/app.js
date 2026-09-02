const ui = {
  start: document.querySelector("#startBtn"),
  accessField: document.querySelector("#accessField"),
  accessKey: document.querySelector("#accessKey"),
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
  latestLatency: document.querySelector("#latestLatency"),
  p50Latency: document.querySelector("#p50Latency"),
  p95Latency: document.querySelector("#p95Latency"),
  sampleCount: document.querySelector("#sampleCount"),
  traceText: document.querySelector("#traceText"),
  traceStages: document.querySelector("#traceStages"),
  networkSummary: document.querySelector("#networkSummary"),
  exportMetrics: document.querySelector("#exportMetricsBtn"),
  clearMetrics: document.querySelector("#clearMetricsBtn"),
};

const SPEECH_VOLUME_THRESHOLD = 0.025;
const SPEECH_START_SAMPLES = 2;
const METER_INTERVAL_MS = 100;

const runtime = {
  session: null,
  socket: null,
  rtcClient: null,
  microphone: null,
  muted: false,
  meterTimer: null,
  accessProtected: true,
  accessToken: sessionStorage.getItem("asrAccessToken") || "",
  speech: createSpeechState(),
  observations: [],
  traces: new Map(),
  firstPartialMs: new Map(),
  networkQuality: null,
  manualCommitAt: null,
};

ui.accessKey.value = runtime.accessToken;

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

function createSpeechState() {
  return {
    active: false,
    consecutiveSamples: 0,
    speechStartAt: null,
    lastVoiceAt: null,
  };
}

function asFiniteNumber(value) {
  if (value === null || value === undefined || value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function roundMetric(value, digits = 2) {
  const number = asFiniteNumber(value);
  if (number === null) return null;
  const scale = 10 ** digits;
  return Math.round(number * scale) / scale;
}

function formatMs(value) {
  const number = asFiniteNumber(value);
  if (number === null) return "—";
  if (number >= 1000) return `${(number / 1000).toFixed(2)}s`;
  return `${Math.round(number)}ms`;
}

function percentile(values, ratio) {
  const sorted = values
    .map(asFiniteNumber)
    .filter((value) => value !== null)
    .sort((left, right) => left - right);
  if (!sorted.length) return null;
  const position = (sorted.length - 1) * ratio;
  const lower = Math.floor(position);
  const upper = Math.ceil(position);
  if (lower === upper) return sorted[lower];
  const weight = position - lower;
  return sorted[lower] * (1 - weight) + sorted[upper] * weight;
}

function mergeObjects(target, source) {
  if (!source || typeof source !== "object" || Array.isArray(source)) return target;
  Object.entries(source).forEach(([key, value]) => {
    if (value && typeof value === "object" && !Array.isArray(value)) {
      const current = target[key];
      target[key] = mergeObjects(
        current && typeof current === "object" && !Array.isArray(current) ? current : {},
        value,
      );
    } else {
      target[key] = value;
    }
  });
  return target;
}

function observeSpeechLevel(volume, observedAt) {
  if (runtime.muted) return;
  const speech = runtime.speech;
  if (volume >= SPEECH_VOLUME_THRESHOLD) {
    speech.consecutiveSamples += 1;
    if (!speech.active && speech.consecutiveSamples >= SPEECH_START_SAMPLES) {
      speech.active = true;
      speech.speechStartAt = observedAt - (SPEECH_START_SAMPLES - 1) * METER_INTERVAL_MS;
    }
    if (speech.active) speech.lastVoiceAt = observedAt;
  } else if (!speech.active) {
    speech.consecutiveSamples = 0;
  }
}

function currentLocalAudioStats() {
  try {
    const raw = runtime.rtcClient?.getLocalAudioStats?.() || {};
    const direct = raw && typeof raw === "object" ? raw : {};
    const nested = Object.values(direct).find(
      (value) => value && typeof value === "object" && !Array.isArray(value),
    );
    const stats = "sendBitrate" in direct || "currentPacketLossRate" in direct
      ? direct
      : (nested || {});
    return {
      sendBitrateBps: asFiniteNumber(stats.sendBitrate),
      sendPackets: asFiniteNumber(stats.sendPackets),
      sendPacketsLost: asFiniteNumber(stats.sendPacketsLost),
      currentPacketLossRate: asFiniteNumber(stats.currentPacketLossRate),
      codecType: stats.codecType || null,
      uplinkNetworkQuality: asFiniteNumber(runtime.networkQuality?.uplinkNetworkQuality),
      downlinkNetworkQuality: asFiniteNumber(runtime.networkQuality?.downlinkNetworkQuality),
    };
  } catch (error) {
    log("读取 Agora 本地音频统计失败", { error: error.message });
    return {};
  }
}

function traceFor(event) {
  if (!event.utteranceId) return null;
  let trace = runtime.traces.get(event.utteranceId);
  if (!trace) {
    trace = {
      schemaVersion: 1,
      recordedAt: new Date().toISOString(),
      sessionId: event.sessionId || runtime.session?.sessionId || null,
      utteranceId: event.utteranceId,
      seq: event.seq ?? null,
      text: "",
      metrics: {},
      complete: false,
    };
    runtime.traces.set(event.utteranceId, trace);
  }
  if (event.seq !== undefined) trace.seq = event.seq;
  if (event.metrics) mergeObjects(trace.metrics, event.metrics);
  return trace;
}

function updateDerivedMetrics(trace) {
  if (!trace) return null;
  const metrics = trace.metrics || {};
  const bridge = metrics.bridge || {};
  const endpointMs = asFiniteNumber(bridge.endpointMs);
  const asrTotalMs = asFiniteNumber(bridge.asrTotalMs);
  if (endpointMs === null || asrTotalMs === null) return trace;

  const optionalComponents = [
    metrics.agora?.networkTransportDelayMs,
    metrics.agora?.jitterBufferDelayMs,
    bridge.audioQueueMs,
    bridge.resultWebSocketSendMs,
    metrics.vps?.relayQueueMs,
    metrics.delivery?.estimatedVpsToBrowserMs,
    metrics.browser?.renderMs,
  ];
  const estimatedEndToEndMs = optionalComponents.reduce(
    (total, value) => total + (asFiniteNumber(value) ?? 0),
    endpointMs + asrTotalMs,
  );
  mergeObjects(trace.metrics, {
    summary: {
      estimatedEndToEndMs: roundMetric(estimatedEndToEndMs),
      method: "component-sum-v1",
    },
  });
  return trace;
}

function sendResultAck(event) {
  const socket = runtime.socket;
  if (
    !socket
    || socket.readyState !== WebSocket.OPEN
    || !event.utteranceId
    || event.seq === undefined
  ) return;
  socket.send(JSON.stringify({
    type: "client.result_ack",
    sessionId: event.sessionId,
    utteranceId: event.utteranceId,
    eventType: event.type,
    seq: event.seq,
  }));
}

function recordPartialTrace(event, receivedAt) {
  const trace = traceFor(event);
  if (!trace) return;
  if (!runtime.firstPartialMs.has(event.utteranceId) && runtime.speech.speechStartAt !== null) {
    runtime.firstPartialMs.set(
      event.utteranceId,
      roundMetric(receivedAt - runtime.speech.speechStartAt),
    );
  }
  sendResultAck(event);
}

function recordFinalTrace(event, receivedAt, renderMs) {
  const trace = traceFor(event);
  if (!trace) return;
  trace.text = event.text || "";
  const speech = runtime.speech;
  const browserMetrics = {
    speechStartToFinalMs: speech.speechStartAt === null
      ? null
      : roundMetric(receivedAt - speech.speechStartAt),
    speechEndToFinalMs: speech.lastVoiceAt === null
      ? null
      : roundMetric(receivedAt - speech.lastVoiceAt),
    firstPartialMs: runtime.firstPartialMs.get(event.utteranceId) ?? null,
    renderMs: roundMetric(renderMs),
    speechVolumeThreshold: SPEECH_VOLUME_THRESHOLD,
    meterIntervalMs: METER_INTERVAL_MS,
    manualCommitToFinalMs: runtime.manualCommitAt === null
      ? null
      : roundMetric(receivedAt - runtime.manualCommitAt),
    resultReceivedAtUnixMs: Date.now(),
  };
  mergeObjects(trace.metrics, {
    browser: browserMetrics,
    agoraClient: currentLocalAudioStats(),
  });
  updateDerivedMetrics(trace);
  if (!trace.complete) {
    trace.complete = true;
    runtime.observations.push(trace);
  }
  runtime.speech = createSpeechState();
  runtime.manualCommitAt = null;
  runtime.firstPartialMs.delete(event.utteranceId);
  sendResultAck(event);
  renderObservability(trace);
}

function applyTraceUpdate(event) {
  const trace = traceFor(event);
  updateDerivedMetrics(trace);
  if (trace) renderObservability(trace.complete ? trace : runtime.observations.at(-1));
}

function traceStages(trace) {
  const metrics = trace?.metrics || {};
  const bridge = metrics.bridge || {};
  const agora = metrics.agora || {};
  const delivery = metrics.delivery || {};
  const browser = metrics.browser || {};
  const vps = metrics.vps || {};
  return [
    ["Agora 网络传输", agora.networkTransportDelayMs, true],
    ["Agora 抖动缓冲", agora.jitterBufferDelayMs, false],
    ["Bridge 断句", bridge.endpointMs, false],
    ["Bridge 音频排队", bridge.audioQueueMs, false],
    ["ASR 请求准备", bridge.asrRequestPrepareMs, false],
    ["OminiX HTTP 往返", bridge.asrHttpRoundTripMs, false],
    ["ASR 响应解析", bridge.asrResponseParseMs, false],
    ["Bridge 文字发送", bridge.resultWebSocketSendMs, false],
    ["VPS 转发排队", vps.relayQueueMs, false],
    ["VPS→浏览器（估算）", delivery.estimatedVpsToBrowserMs, true],
    ["浏览器渲染", browser.renderMs, false],
    ["立即断句→文字", browser.manualCommitToFinalMs, false],
  ].filter(([, value]) => asFiniteNumber(value) !== null);
}

function renderObservability(preferredTrace = null) {
  runtime.observations.forEach(updateDerivedMetrics);
  updateDerivedMetrics(preferredTrace);
  const latest = preferredTrace || runtime.observations.at(-1) || null;
  const tails = runtime.observations
    .map((trace) => asFiniteNumber(trace.metrics?.summary?.estimatedEndToEndMs))
    .filter((value) => value !== null);
  const latestTail = asFiniteNumber(latest?.metrics?.summary?.estimatedEndToEndMs);
  ui.latestLatency.textContent = formatMs(latestTail);
  ui.p50Latency.textContent = formatMs(percentile(tails, 0.5));
  ui.p95Latency.textContent = formatMs(percentile(tails, 0.95));
  ui.sampleCount.textContent = String(tails.length);
  ui.exportMetrics.disabled = runtime.observations.length === 0;
  ui.clearMetrics.disabled = runtime.observations.length === 0;

  if (!latest) return;
  ui.traceText.textContent = latest.text || "指标更新中…";
  const agora = latest.metrics?.agora || {};
  const client = latest.metrics?.agoraClient || {};
  const networkParts = [];
  if (asFiniteNumber(agora.audioLossRatePercent) !== null) {
    networkParts.push(`Bridge 收音丢包 ${agora.audioLossRatePercent}%`);
  }
  if (asFiniteNumber(client.uplinkNetworkQuality) !== null) {
    networkParts.push(`浏览器上行等级 ${client.uplinkNetworkQuality}`);
  }
  if (asFiniteNumber(agora.receivedBitrateKbps) !== null) {
    networkParts.push(`接收码率 ${agora.receivedBitrateKbps}kbps`);
  }
  ui.networkSummary.textContent = networkParts.length
    ? `Agora 网络统计：${networkParts.join(" · ")}`
    : "Agora 网络统计：当前 SDK 尚未上报样本";

  const stages = traceStages(latest);
  if (!stages.length) {
    ui.traceStages.innerHTML = '<p class="trace-empty">该结果尚未携带分段指标。</p>';
    return;
  }
  const maximum = Math.max(...stages.map(([, value]) => Number(value)), 1);
  ui.traceStages.replaceChildren(...stages.map(([label, value, estimated]) => {
    const row = document.createElement("div");
    row.className = `trace-stage${estimated ? " estimated" : ""}`;
    const name = document.createElement("span");
    name.textContent = label;
    const bar = document.createElement("span");
    bar.className = "trace-bar";
    const fill = document.createElement("i");
    fill.style.width = `${Math.max(2, Math.min(100, Number(value) / maximum * 100))}%`;
    bar.append(fill);
    const duration = document.createElement("strong");
    duration.textContent = formatMs(value);
    row.append(name, bar, duration);
    return row;
  }));
}

function exportObservations() {
  if (!runtime.observations.length) return;
  const report = {
    schemaVersion: 1,
    exportedAt: new Date().toISOString(),
    measurement: {
      speechVolumeThreshold: SPEECH_VOLUME_THRESHOLD,
      meterIntervalMs: METER_INTERVAL_MS,
      primaryMetric: "metrics.summary.estimatedEndToEndMs",
      note: "Primary latency is the sum of same-utterance stages; network one-way latency is estimated from RTT. Browser volume timing is diagnostic only.",
    },
    observations: runtime.observations,
  };
  const blob = new Blob([JSON.stringify(report, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `agora-asr-trace-${new Date().toISOString().replaceAll(":", "-")}.json`;
  link.click();
  URL.revokeObjectURL(url);
}

function clearObservations() {
  runtime.observations = [];
  runtime.traces.clear();
  runtime.firstPartialMs.clear();
  ui.traceText.textContent = "尚未收到最终识别结果。";
  ui.networkSummary.textContent = "Agora 网络统计：等待数据";
  ui.traceStages.innerHTML = '<p class="trace-empty">开始识别后，这里会展示各阶段耗时。</p>';
  renderObservability();
}

async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (runtime.accessToken && options.auth !== false) {
    headers.Authorization = `Bearer ${runtime.accessToken}`;
  }
  const response = await fetch(path, {
    ...options,
    headers,
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.error?.message || `${response.status} ${response.statusText}`);
  }
  return response.status === 204 ? null : response.json();
}

async function refreshStatus() {
  try {
    const status = await api("/api/v1/status", { auth: false });
    runtime.accessProtected = status.accessProtected;
    ui.accessField.hidden = !status.accessProtected;
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
    const receivedAt = performance.now();
    const payload = JSON.parse(event.data);
    log(`收到 ${payload.type}`, payload.text ? { text: payload.text } : undefined);
    handleEvent(payload, receivedAt);
  });
  socket.addEventListener("close", () => log("文本事件 WebSocket 已断开"));
  socket.addEventListener("error", () => log("文本事件 WebSocket 异常"));
}

function handleEvent(event, receivedAt = performance.now()) {
  if (event.type === "session.snapshot") {
    if (event.state === "ready") markReady();
    return;
  }
  if (event.type === "session.ready") {
    markReady();
    return;
  }
  if (event.type === "asr.partial") {
    recordPartialTrace(event, receivedAt);
    ui.partial.textContent = event.text || "";
    setDot(ui.asrDot, "busy");
    return;
  }
  if (event.type === "asr.final") {
    ui.partial.textContent = "";
    const renderStartedAt = performance.now();
    appendFinal(event.text || "");
    recordFinalTrace(event, receivedAt, performance.now() - renderStartedAt);
    setDot(ui.asrDot, "on");
    return;
  }
  if (event.type === "trace.update") {
    applyTraceUpdate(event);
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
    return;
  }
  if (event.type === "session.expired") {
    appendFinal("⚠ 会话已到期，请重新开始识别。");
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
  client.on("network-quality", (quality) => {
    runtime.networkQuality = quality;
  });
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
    observeSpeechLevel(volume, performance.now());
    ui.levelBars.forEach((bar, index) => {
      const threshold = index / ui.levelBars.length;
      bar.style.height = `${5 + (volume > threshold ? 17 * Math.min(1, volume + 0.25) : 0)}px`;
    });
  }, METER_INTERVAL_MS);
}

async function start() {
  runtime.accessToken = ui.accessKey.value.trim();
  if (runtime.accessProtected && !runtime.accessToken) {
    appendFinal("⚠ 请先填写管理员提供的访问密钥。");
    ui.accessKey.focus();
    return;
  }
  if (runtime.accessToken) sessionStorage.setItem("asrAccessToken", runtime.accessToken);
  ui.start.disabled = true;
  runtime.speech = createSpeechState();
  runtime.manualCommitAt = null;
  runtime.networkQuality = null;
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
  const committedAt = performance.now();
  runtime.manualCommitAt = committedAt;
  try {
    await api(`/api/v1/sessions/${runtime.session.sessionId}/commit`, {
      method: "POST",
      body: "{}",
    });
    log("已请求立即断句");
  } catch (error) {
    if (runtime.manualCommitAt === committedAt) runtime.manualCommitAt = null;
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
  runtime.speech = createSpeechState();
  runtime.networkQuality = null;
  runtime.manualCommitAt = null;
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
ui.accessKey.addEventListener("change", () => {
  runtime.accessToken = ui.accessKey.value.trim();
  if (runtime.accessToken) sessionStorage.setItem("asrAccessToken", runtime.accessToken);
  else sessionStorage.removeItem("asrAccessToken");
});
ui.mute.addEventListener("click", toggleMute);
ui.commit.addEventListener("click", commit);
ui.stop.addEventListener("click", stop);
ui.exportMetrics.addEventListener("click", exportObservations);
ui.clearMetrics.addEventListener("click", clearObservations);
window.addEventListener("pagehide", () => {
  runtime.microphone?.close();
  if (!runtime.session) return;
  const headers = runtime.accessToken
    ? { Authorization: `Bearer ${runtime.accessToken}` }
    : {};
  fetch(`/api/v1/sessions/${runtime.session.sessionId}`, {
    method: "DELETE",
    headers,
    keepalive: true,
  }).catch(() => {});
});

refreshStatus();
renderObservability();
setInterval(refreshStatus, 5000);

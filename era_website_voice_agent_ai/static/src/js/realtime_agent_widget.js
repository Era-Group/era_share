/** @odoo-module **/

// Disable transcript processing to speed up voice conversations
const DISABLE_TRANSCRIPT = true;
const UI_WIRED_KEY = "__eraRealtimeWidgetUiWired";
const DEFAULT_PTT_IDLE_TIMEOUT_SECONDS = 30;

let state = {
  running: false,
  pc: null,
  dc: null,
  micStream: null,
  audioEl: null,
  pendingText: null,
  startedAt: null,
  transcript: [],
  assistantBuffer: "",
  sessionMeta: null,
  remoteStream: null,
  audioContext: null,
  mixerDest: null,
  recorder: null,
  recordedChunks: [],
  recordingFinalized: false,
  summaryPayload: null,
  finalizeContext: null,
  unloadHandled: false,
  lifecycleWired: false,
  recorderFlushTimer: null,
  summaryId: null,
  sessionKey: "",
  clientCallId: "",
  chunkSeq: 0,
  chunkUploadChain: Promise.resolve(),
  starting: false,
  stopping: false,
  responseInFlight: false,
  micEnabled: false,
  pttIdleTimer: null,
  pttLastActivityAt: 0,
};

function qs(id) {
  return document.getElementById(id);
}

function notifyEmbedHost() {
  if (!window.parent || window.parent === window) return;
  const panel = qs("oai-agent-panel");
  const isOpen = !!(panel && !panel.classList.contains("d-none"));
  const payload = {
    source: "era-realtime-widget",
    type: "resize",
    width: isOpen ? 360 : 92,
    height: isOpen ? 560 : 92,
    open: isOpen,
  };
  try {
    window.parent.postMessage(payload, "*");
  } catch (_err) {
    // no-op
  }
}

function widgetEl() {
  return document.getElementById("oai-agent-widget");
}

function createClientCallId() {
  const rand = Math.random().toString(36).slice(2, 12);
  return `call_${Date.now().toString(36)}_${rand}`;
}

function getConfig() {
  const w = widgetEl();
  const rawVoice = w?.dataset?.voice || "";
  const voice = (!rawVoice || rawVoice === "marin") ? "marin" : rawVoice;
  const embedModeRaw = w?.dataset?.embedMode || "";
  const embedMode = String(embedModeRaw).toLowerCase() === "1" || String(embedModeRaw).toLowerCase() === "true";
  return {
    promptId: w?.dataset?.promptId || "",
    model: w?.dataset?.model || "gpt-realtime-mini",
    voice: voice,
    callerPhone: w?.dataset?.callerPhone || "",
    callerCompany: w?.dataset?.callerCompany || "",
    embedMode: embedMode,
  };
}

function toWesternDigits(value) {
  return String(value || "")
    .replace(/[٠-٩]/g, (ch) => String(ch.charCodeAt(0) - 0x660))
    .replace(/[۰-۹]/g, (ch) => String(ch.charCodeAt(0) - 0x6f0));
}

function normalizeMobileNumber(value) {
  const raw = toWesternDigits(value).trim();
  if (!raw) return "";
  let normalized = raw.replace(/[\s\-().]/g, "");
  normalized = normalized.replace(/(?!^)\+/g, "");
  if (!/^\+?\d+$/.test(normalized)) return "";
  const digitCount = normalized.replace(/\D/g, "").length;
  if (digitCount < 7 || digitCount > 20) return "";
  return normalized;
}

function showMobileStep(show) {
  const el = qs("oai-agent-mobile-step");
  if (el) el.classList.toggle("d-none", !show);
}

function showCallActions(show) {
  const el = qs("oai-agent-call-actions");
  if (el) el.classList.toggle("d-none", !show);
}

function setMobileError(text = "") {
  const el = qs("oai-agent-mobile-error");
  if (!el) return;
  if (text) {
    el.textContent = text;
    el.classList.remove("d-none");
    return;
  }
  el.textContent = "";
  el.classList.add("d-none");
}

function setStartButtonLoading(loading) {
  const btn = qs("oai-agent-start");
  if (!btn) return;
  btn.disabled = !!loading;
  btn.textContent = loading ? "جاري الاتصال..." : "متابعة";
}

function prepareMobileStep() {
  const input = qs("oai-agent-mobile-input");
  const cfg = getConfig();
  if (input) {
    input.value = cfg.callerPhone || "";
    setTimeout(() => input.focus(), 0);
  }
  setMobileError("");
  setStartButtonLoading(false);
  showMobileStep(true);
  showCallActions(false);
  setStatus("أدخل رقم الجوال للمتابعة");
}

function collectVisitorMobileNumber() {
  const input = qs("oai-agent-mobile-input");
  const normalized = normalizeMobileNumber(input?.value || "");
  if (!normalized) {
    setMobileError("يرجى إدخال رقم جوال صحيح للمتابعة.");
    if (input) input.focus();
    return "";
  }
  if (input) input.value = normalized;
  setMobileError("");
  return normalized;
}

async function startAgentFromPanel() {
  if (state.starting || state.running) return;
  const mobileNumber = collectVisitorMobileNumber();
  if (!mobileNumber) return;
  const w = widgetEl();
  if (w) w.dataset.callerPhone = mobileNumber;

  setStartButtonLoading(true);
  state.starting = true;
  setStatus("جاري الاتصال...");
  try {
    await startAgent();
    showMobileStep(false);
    showCallActions(true);
    updatePushToTalkButton();
  } finally {
    state.starting = false;
    setStartButtonLoading(false);
  }
}

function setStatus(text) {
  const el = qs("oai-agent-status");
  if (el) el.textContent = text;
}

function clearPttIdleTimer() {
  if (state.pttIdleTimer) {
    clearTimeout(state.pttIdleTimer);
    state.pttIdleTimer = null;
  }
}

function resolvePttIdleTimeoutSeconds() {
  const configured = Number(state.sessionMeta?.idleTimeoutSeconds || 0);
  if (Number.isFinite(configured) && configured > 0) {
    return Math.max(DEFAULT_PTT_IDLE_TIMEOUT_SECONDS, configured);
  }
  return DEFAULT_PTT_IDLE_TIMEOUT_SECONDS;
}

function armPttIdleTimer() {
  clearPttIdleTimer();
  if (!state.running || state.stopping || state.micEnabled) return;
  const timeoutSeconds = resolvePttIdleTimeoutSeconds();
  const timeoutMs = Math.max(1, Math.round(timeoutSeconds * 1000));
  state.pttIdleTimer = setTimeout(() => {
    if (!state.running || state.stopping || state.micEnabled) return;
    stopAgent(`تم إنهاء المكالمة بسبب عدم الضغط على زر التحدث لمدة ${timeoutSeconds} ثانية.`);
  }, timeoutMs);
}

function touchPttActivity() {
  state.pttLastActivityAt = Date.now();
  clearPttIdleTimer();
}

function updatePushToTalkButton() {
  const btn = qs("oai-agent-ptt");
  if (!btn) return;
  const active = !!(state.running && state.micEnabled);
  btn.disabled = !state.running;
  btn.setAttribute("aria-pressed", active ? "true" : "false");
  btn.classList.toggle("is-live", active);
  btn.title = active ? "Talking..." : "Push to talk";
  const label = qs("oai-agent-ptt-label");
  if (label) label.textContent = active ? "Talking" : "Push to talk";
}

function setMicEnabled(enabled) {
  const tracks = state.micStream?.getAudioTracks?.() || [];
  const target = !!enabled;
  if (tracks.length) {
    tracks.forEach((track) => {
      track.enabled = target;
    });
  }
  state.micEnabled = !!(target && tracks.length);
  updatePushToTalkButton();
}

function startPushToTalk(ev) {
  if (ev) ev.preventDefault();
  if (!state.running) return;
  touchPttActivity();
  setMicEnabled(true);
}

function stopPushToTalk(ev) {
  if (ev && (ev.type === "keydown" || ev.type === "keyup")) ev.preventDefault();
  setMicEnabled(false);
  armPttIdleTimer();
}

function appendTranscript(role, text) {
  const container = qs("oai-agent-transcript");
  if (!container) return;
  const msg = document.createElement("div");
  msg.className = `oai-agent-msg oai-agent-msg--${role}`;
  msg.textContent = text;
  container.appendChild(msg);
  container.scrollTop = container.scrollHeight;
}

function togglePanel(show) {
  const panel = qs("oai-agent-panel");
  if (!panel) return;
  panel.classList.toggle("d-none", !show);
  notifyEmbedHost();
}

function errorToText(err) {
  if (!err) return "Unknown error";
  if (typeof err === "string") return err;
  if (err instanceof Error && err.message) return err.message;
  const rpcError = err.error || err.data || err.cause || null;
  if (rpcError) {
    if (typeof rpcError === "string") return rpcError;
    if (rpcError.message) return String(rpcError.message);
    if (rpcError.error?.message) return String(rpcError.error.message);
  }
  if (err.message) {
    if (typeof err.message === "string") return err.message;
    if (err.message?.message) return String(err.message.message);
    try {
      return JSON.stringify(err.message);
    } catch (_jsonErr) {
      // no-op
    }
  }
  try {
    return JSON.stringify(err);
  } catch (_jsonErr) {
    return String(err);
  }
}

async function rpcJson(url, params = {}) {
  const payload = {
    jsonrpc: "2.0",
    method: "call",
    params: params,
    id: Math.floor(Math.random() * 1000000),
  };

  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const raw = await res.text();
  let json = null;
  try {
    json = raw ? JSON.parse(raw) : {};
  } catch (_err) {
    throw new Error(`Non-JSON response from ${url} (status ${res.status}): ${raw.slice(0, 200)}`);
  }
  if (json && json.error) {
    const rpcErr = json.error || {};
    const data = rpcErr.data || {};
    const msg =
      data.message ||
      data.arguments?.[0] ||
      rpcErr.message ||
      `RPC error from ${url}`;
    throw new Error(String(msg));
  }
  if (json && Object.prototype.hasOwnProperty.call(json, "result")) {
    return json.result;
  }
  return json;
}

function safeSend(obj) {
  if (!state.dc || state.dc.readyState !== "open") return false;
  state.dc.send(JSON.stringify(obj));
  return true;
}

function trackRealtimeResponseState(evtType) {
  const type = String(evtType || "");
  if (!type) return;
  if (
    type === "response.created" ||
    type === "response.text.delta" ||
    type === "response.output_text.delta" ||
    type === "response.audio.delta"
  ) {
    state.responseInFlight = true;
    return;
  }
  if (
    type === "response.done" ||
    type === "response.completed" ||
    type === "response.failed" ||
    type === "response.cancelled" ||
    type === "response.canceled"
  ) {
    state.responseInFlight = false;
  }
}

function classifyNonFatalRealtimeError(errCode, message) {
  const code = String(errCode || "").toLowerCase();
  const msg = String(message || "").toLowerCase();
  if (
    code === "conversation_already_has_active_response" ||
    msg.includes("already has an active response in progress")
  ) {
    state.responseInFlight = true;
    return true;
  }
  if (
    code === "invalid_value" ||
    msg.includes("audio content of")
  ) {
    if (/audio content of .* shorter than/i.test(msg)) {
      return true;
    }
  }
  return false;
}

function clearRecorderFlushTimer() {
  if (state.recorderFlushTimer) {
    clearInterval(state.recorderFlushTimer);
    state.recorderFlushTimer = null;
  }
}

function blobToBase64(blob) {
  return new Promise((resolve) => {
    const reader = new FileReader();
    reader.onloadend = () => {
      const dataUrl = reader.result || "";
      const parts = String(dataUrl).split(",");
      resolve(parts.length > 1 ? parts[1] : "");
    };
    reader.readAsDataURL(blob);
  });
}

function enqueueAudioChunkUpload(blob) {
  if (!blob || blob.size === 0) return;
  if (!state.summaryId || !state.sessionKey) return;
  const mimetype = state.recorder?.mimeType || blob.type || "audio/webm";
  const summaryId = state.summaryId;
  const sessionKey = state.sessionKey;
  const seq = state.chunkSeq++;
  state.chunkUploadChain = state.chunkUploadChain
    .then(async () => {
      const base64 = await blobToBase64(blob);
      if (!base64) return;
      const res = await rpcJson("/realtime_agent/chunk", {
        summary_id: summaryId,
        session_key: sessionKey,
        embed_mode: state.sessionMeta?.embedMode ? "1" : "",
        audio_chunk_base64: base64,
        audio_mimetype: mimetype,
        chunk_seq: seq,
      });
      if (res?.error) {
        console.warn("Audio chunk upload error:", res.error, res.details || "");
      }
    })
    .catch((err) => {
      console.warn("Audio chunk upload failed:", err);
    });
}

function initRecorder(micStream) {
  if (!micStream) return;
  try {
    const audioContext = new (window.AudioContext || window.webkitAudioContext)();
    const dest = audioContext.createMediaStreamDestination();
    const micSource = audioContext.createMediaStreamSource(micStream);
    micSource.connect(dest);

    state.audioContext = audioContext;
    state.mixerDest = dest;
    state.recordedChunks = [];
    state.recordingFinalized = false;

    const preferredMime = "audio/mpeg";
    const mimeType = MediaRecorder.isTypeSupported(preferredMime) ? preferredMime : "audio/webm";
    const recorder = new MediaRecorder(dest.stream, { mimeType: mimeType });
    recorder.ondataavailable = (e) => {
      if (e.data && e.data.size > 0) {
        state.recordedChunks.push(e.data);
        enqueueAudioChunkUpload(e.data);
      }
    };
    recorder.onstop = () => {
      finalizeRecording();
    };
    state.recorder = recorder;
    recorder.start(5000);
    clearRecorderFlushTimer();
    // Force periodic chunk flushes; some browsers delay chunks until stop.
    state.recorderFlushTimer = setInterval(() => {
      if (state.recorder && state.recorder.state === "recording") {
        try {
          state.recorder.requestData();
        } catch (err) {
          // no-op: requestData can fail during teardown
        }
      }
    }, 5000);
  } catch (err) {
    console.warn("Recorder init failed:", err);
  }
}

function attachRemoteToMixer(stream) {
  if (!stream || !state.audioContext || !state.mixerDest) return;
  try {
    const remoteSource = state.audioContext.createMediaStreamSource(stream);
    remoteSource.connect(state.mixerDest);
  } catch (err) {
    console.warn("Remote mixer attach failed:", err);
  }
}

function pushTranscript(role, text) {
  if (DISABLE_TRANSCRIPT) return;
  const cleaned = (text || "").trim();
  if (!cleaned) return;
  state.transcript.push({
    role: role,
    text: cleaned,
    ts: Date.now(),
  });
  appendTranscript(role, cleaned);
}

function sendUserText(text) {
  const trimmed = (text || "").trim();
  if (!trimmed) return;
  if (state.responseInFlight) {
    // Keep only the latest queued utterance while a response is active.
    state.pendingText = trimmed;
    return;
  }

  pushTranscript("user", trimmed);

  if (!safeSend({
    type: "conversation.item.create",
    item: {
      type: "message",
      role: "user",
      content: [{ type: "input_text", text: trimmed }],
    },
  })) {
    state.pendingText = trimmed;
    return;
  }

  if (!safeSend({
    type: "response.create",
    response: {
      modalities: ["audio", "text"],
    },
  })) {
    state.pendingText = trimmed;
    return;
  }
  state.responseInFlight = true;
}

async function startAgent() {
  if (state.running || state.starting && state.pc) return;
  const cfg = getConfig();
  state.sessionMeta = {
    promptId: cfg.promptId,
    model: cfg.model,
    voice: cfg.voice,
    callerPhone: cfg.callerPhone,
    callerCompany: cfg.callerCompany,
    embedMode: cfg.embedMode,
  };
  state.startedAt = Date.now();
  state.unloadHandled = false;
  state.stopping = false;
  state.finalizeContext = null;
  state.summaryId = null;
  state.sessionKey = "";
  state.clientCallId = createClientCallId();
  state.chunkSeq = 0;
  state.chunkUploadChain = Promise.resolve();
  state.transcript = [];
  state.assistantBuffer = "";
  if (!DISABLE_TRANSCRIPT) {
    const transcriptEl = qs("oai-agent-transcript");
    if (transcriptEl) transcriptEl.innerHTML = "";
  }
  setStatus("جاري التجهيز...");
  const session = await rpcJson("/realtime_agent/session_start", {
    prompt_id: cfg.promptId,
    model: cfg.model,
    voice: cfg.voice,
    caller_phone: cfg.callerPhone,
    caller_company: cfg.callerCompany,
    client_call_id: state.clientCallId,
    embed_mode: cfg.embedMode ? "1" : "",
  });
  if (!session || session.error) {
    console.error("Session start failed:", session?.error || "unknown", session?.details || "");
    const details = session?.details ? ` (${session.details})` : "";
    throw new Error((session?.error || "Could not start call session.") + details);
  }
  state.summaryId = session.summary_id || null;
  state.sessionKey = session.session_key || "";

  const tok = await rpcJson("/realtime_agent/token", {
    embed_mode: cfg.embedMode ? "1" : "",
    client_call_id: state.clientCallId,
    summary_id: state.summaryId || "",
    session_key: state.sessionKey || "",
  });
  const promptFallback = !!tok?.prompt_fallback;
  const interruptResponseEnabled = !!tok?.interrupt_response_enabled;
  const idleTimeoutSeconds = Number(tok?.idle_timeout_seconds || 0);
  state.sessionMeta.promptFallback = promptFallback;
  state.sessionMeta.interruptResponseEnabled = interruptResponseEnabled;
  state.sessionMeta.idleTimeoutSeconds = Number.isFinite(idleTimeoutSeconds) ? idleTimeoutSeconds : 0;

  if (!tok || tok.error) {
    console.error("Token error:", tok?.error || "Unknown error", tok?.details || "");
    const details = tok?.details ? ` (${tok.details})` : "";
    throw new Error((tok?.error || "Could not retrieve ephemeral token from server.") + details);
  }

  const EPHEMERAL_KEY = tok.value;
  if (!EPHEMERAL_KEY) {
    console.error("Token response is missing value:", tok);
    throw new Error("Invalid token response from server.");
  }

  const pc = new RTCPeerConnection();

  const audioEl = document.createElement("audio");
  audioEl.autoplay = true;
  audioEl.className = "oai-agent-audio";
  document.body.appendChild(audioEl);

  pc.ontrack = (e) => {
    audioEl.srcObject = e.streams[0];
    state.remoteStream = e.streams[0];
    attachRemoteToMixer(state.remoteStream);
    audioEl.play().catch(err => console.error("Audio play failed:", err));
  };

  const micStream = await navigator.mediaDevices.getUserMedia({ audio: true });
  state.micStream = micStream;
  micStream.getTracks().forEach((t) => pc.addTrack(t, micStream));
  // Push-to-talk: keep microphone muted until the user presses the PTT button.
  setMicEnabled(false);
  initRecorder(micStream);

  const dc = pc.createDataChannel("oai-events");
  dc.addEventListener("message", (e) => {
    try {
      const evt = JSON.parse(e.data);
      trackRealtimeResponseState(evt.type);
      if (!DISABLE_TRANSCRIPT && evt.type && evt.type.includes("input_audio_transcription")) {
        const userText =
          evt.transcript ||
          evt.text ||
          evt.content?.text ||
          evt.item?.content?.[0]?.transcript ||
          evt.item?.content?.[0]?.text ||
          "";
        if (userText) pushTranscript("user", userText);
      }
      const isTextDelta =
        evt.type === "response.output_text.delta" ||
        evt.type === "response.text.delta" ||
        (evt.type && evt.type.includes("transcript") && evt.type.endsWith(".delta"));
      if (!DISABLE_TRANSCRIPT && isTextDelta) {
        const delta = evt.delta || evt.text || evt.content?.text || "";
        if (delta) state.assistantBuffer += delta;
      }
      const isTextDone =
        evt.type === "response.output_text.done" ||
        evt.type === "response.text.done" ||
        (evt.type && evt.type.includes("transcript") && evt.type.endsWith(".done"));
      if (!DISABLE_TRANSCRIPT && isTextDone) {
        const finalText = (evt.text || evt.content?.text || state.assistantBuffer || "").trim();
        if (finalText) pushTranscript("assistant", finalText);
        state.assistantBuffer = "";
      }
      if (evt.type === "error") {
        const err = evt.error || {};
        const errMsg =
          err.message ||
          err.error?.message ||
          err.code ||
          err.type ||
          JSON.stringify(err);

        const errCode = String(err.code || err.error?.code || "").toLowerCase();
        const msg = String(errMsg || "");
        if (state.stopping || !state.running) {
          return;
        }
        if (classifyNonFatalRealtimeError(errCode, msg)) {
          // Non-fatal realtime edge cases; keep the session running silently.
          return;
        }

        console.error("OpenAI Error:", errMsg, err);

        const isFatal =
          /unauthorized|forbidden|api key|token.*expired|session.*expired|session.*not found/i.test(msg) ||
          /(turn_detection|interrupt_response)/i.test(msg);
        if (isFatal) {
          setStatus(`صار خطأ في الاتصال: ${msg.slice(0, 120)}`);
          stopAgent();
          return;
        }
        if (state.pendingText && !state.responseInFlight) {
          const queued = state.pendingText;
          state.pendingText = null;
          sendUserText(queued);
        }
      }
      if (state.pendingText && !state.responseInFlight && state.dc && state.dc.readyState === "open") {
        const queued = state.pendingText;
        state.pendingText = null;
        sendUserText(queued);
      }
    } catch (err) {
      console.error("Error parsing message:", err);
    }
  });

  const offer = await pc.createOffer();
  await pc.setLocalDescription(offer);
  const modelUrl = cfg.model || "gpt-realtime-mini";
  const sdpResp = await fetch("https://api.openai.com/v1/realtime/calls?model=" + modelUrl, {
    method: "POST",
    body: offer.sdp,
    headers: {
      "Authorization": `Bearer ${EPHEMERAL_KEY.trim()}`,
      "Content-Type": "application/sdp",
      "OpenAI-Beta": "realtime=v1",
    },
  });
  if (!sdpResp.ok) {
    const errorText = await sdpResp.text();
    console.error("OpenAI SDP error:", errorText);
    throw new Error(`Failed to create Realtime call: ${sdpResp.status} ${sdpResp.statusText}`);
  }

  const answerSdp = await sdpResp.text();
  await pc.setRemoteDescription({ type: "answer", sdp: answerSdp });

  dc.addEventListener("open", async () => {
    setStatus("متصل ✅ اضغط مطولًا على زر التحدث");

    const sessionUpdate = {
      model: cfg.model,
      voice: cfg.voice,
      input_audio_transcription: {
        model: "gpt-4o-mini-transcribe",
      },
    };

    safeSend({
      type: "session.update",
      session: sessionUpdate,
    });

    if (state.pendingText) {
      sendUserText(state.pendingText);
      state.pendingText = null;
    }
  });

  pc.addEventListener("connectionstatechange", () => {
    if (pc.connectionState === "failed" || pc.connectionState === "disconnected") {
      setStatus("انقطع الاتصال");
      stopAgent();
    }
  });

  state.running = true;
  state.pc = pc;
  state.dc = dc;
  state.audioEl = audioEl;
  updatePushToTalkButton();
  armPttIdleTimer();
}

async function submitSummary(payload) {
  if (!payload || !payload.transcript) return;
  try {
    const res = await rpcJson("/realtime_agent/summary", payload);
    if (res?.error) {
      console.error("Summary error:", res.error, res.details || "");
      return;
    }
    if (res?.warning) {
      console.warn("Summary warning:", res.warning);
    }
    if (res?.id && !state.summaryId) state.summaryId = res.id;
  } catch (err) {
    console.error("Summary request failed:", err);
  }
}

async function endSession(ctx = null) {
  const context = ctx || {};
  const sessionMeta = context.sessionMeta || state.sessionMeta || {};
  const summaryPayload = context.summaryPayload || state.summaryPayload || null;
  const summaryId = context.summaryId || state.summaryId || "";
  const sessionKey = context.sessionKey || state.sessionKey || "";
  const clientCallId = context.clientCallId || state.clientCallId || "";
  const startedAt = context.startedAt || state.startedAt;
  const durationSeconds = startedAt ? Math.round((Date.now() - startedAt) / 1000) : "";
  if (!summaryId && !sessionKey && !clientCallId) return { ok: false };
  try {
    return await rpcJson("/realtime_agent/session_end", {
      summary_id: summaryId,
      session_key: sessionKey,
      client_call_id: clientCallId,
      transcript: summaryPayload?.transcript || "",
      prompt_id: sessionMeta?.promptId || "",
      model: sessionMeta?.model || "",
      voice: sessionMeta?.voice || "",
      embed_mode: sessionMeta?.embedMode ? "1" : "",
      caller_phone: sessionMeta?.callerPhone || "",
      caller_company: sessionMeta?.callerCompany || "",
      duration_seconds: durationSeconds ? String(durationSeconds) : "",
    });
  } catch (err) {
    console.warn("Session end request failed:", err);
    return { ok: false };
  }
}

function clearFinalizeState() {
  clearRecorderFlushTimer();
  clearPttIdleTimer();
  state.recorder = null;
  state.recordedChunks = [];
  state.summaryPayload = null;
  state.finalizeContext = null;
  state.summaryId = null;
  state.sessionKey = "";
  state.clientCallId = "";
  state.responseInFlight = false;
  state.micEnabled = false;
  state.pttLastActivityAt = 0;
  state.stopping = false;
  if (state.audioContext) {
    state.audioContext.close().catch(() => {});
    state.audioContext = null;
    state.mixerDest = null;
  }
}

function sendJsonRpcBeacon(url, params = {}) {
  if (!navigator.sendBeacon) return false;
  const payload = JSON.stringify({
    jsonrpc: "2.0",
    method: "call",
    params: params,
    id: Math.floor(Math.random() * 1000000),
  });
  try {
    const blob = new Blob([payload], { type: "application/json" });
    return navigator.sendBeacon(url, blob);
  } catch (err) {
    console.warn("Beacon JSON-RPC send failed:", err);
    return false;
  }
}

function buildSummaryPayload() {
  const transcriptText = state.transcript
    .map((item) => `${item.role === "user" ? "User" : "Assistant"}: ${item.text}`)
    .join("\n");
  if (!transcriptText.trim()) return null;
  const durationSeconds = state.startedAt ? Math.round((Date.now() - state.startedAt) / 1000) : null;
  return {
    transcript: transcriptText,
    prompt_id: state.sessionMeta?.promptId || "",
    model: state.sessionMeta?.model || "",
    voice: state.sessionMeta?.voice || "",
    embed_mode: state.sessionMeta?.embedMode ? "1" : "",
    duration_seconds: durationSeconds,
    summary_id: state.summaryId || "",
    session_key: state.sessionKey || "",
    client_call_id: state.clientCallId || "",
  };
}

async function finalizeRecording() {
  if (state.recordingFinalized) return;
  if (!state.recorder) return;
  const ctx = state.finalizeContext || {};
  const sessionMeta = ctx.sessionMeta || state.sessionMeta || {};
  const summaryPayload = ctx.summaryPayload || state.summaryPayload || null;
  const startedAt = ctx.startedAt || state.startedAt;
  const summaryId = ctx.summaryId || state.summaryId || "";
  const sessionKey = ctx.sessionKey || state.sessionKey || "";
  const clientCallId = ctx.clientCallId || state.clientCallId || "";
  const endCtx = {
    sessionMeta: sessionMeta,
    summaryPayload: summaryPayload,
    startedAt: startedAt,
    summaryId: summaryId,
    sessionKey: sessionKey,
    clientCallId: clientCallId,
  };
  if (state.recordedChunks.length === 0) {
    const endRes = await endSession(endCtx);
    if (summaryPayload && !endRes?.attachment_id) {
      submitSummary(summaryPayload);
    }
    clearFinalizeState();
    return;
  }
  state.recordingFinalized = true;
  try {
    await state.chunkUploadChain;
    const endRes = await endSession(endCtx);
    if (summaryPayload && !endRes?.attachment_id) {
      submitSummary(summaryPayload);
    }
  } catch (err) {
    console.warn("Recording finalize failed:", err);
  } finally {
    clearFinalizeState();
  }
}

function handlePageUnload() {
  if (state.unloadHandled || state.recordingFinalized) return;
  if (!state.running && (!state.recorder || state.recordedChunks.length === 0)) return;
  state.unloadHandled = true;

  const summaryPayload = state.summaryPayload || buildSummaryPayload();
  const snapshot = {
    summaryPayload: summaryPayload || null,
    sessionMeta: state.sessionMeta ? { ...state.sessionMeta } : null,
    startedAt: state.startedAt,
    summaryId: state.finalizeContext?.summaryId || state.summaryId,
    sessionKey: state.finalizeContext?.sessionKey || state.sessionKey,
    clientCallId: state.finalizeContext?.clientCallId || state.clientCallId,
  };

  sendJsonRpcBeacon("/realtime_agent/session_abandoned", {
    transcript: summaryPayload?.transcript || "",
    prompt_id: snapshot.sessionMeta?.promptId || "",
    model: snapshot.sessionMeta?.model || "",
    voice: snapshot.sessionMeta?.voice || "",
    embed_mode: snapshot.sessionMeta?.embedMode ? "1" : "",
    caller_phone: snapshot.sessionMeta?.callerPhone || "",
    caller_company: snapshot.sessionMeta?.callerCompany || "",
    duration_seconds: summaryPayload?.duration_seconds || (snapshot.startedAt ? Math.round((Date.now() - snapshot.startedAt) / 1000) : ""),
    summary_id: snapshot.summaryId || "",
    session_key: snapshot.sessionKey || "",
    client_call_id: snapshot.clientCallId || "",
  });

  if (state.recorder && state.recorder.state !== "inactive") {
    try {
      state.recorder.requestData();
    } catch (err) {
      console.warn("Recorder requestData failed during unload:", err);
    }
  }
}

function stopAgent(reasonText = "") {
  if (state.stopping) return;
  state.stopping = true;
  clearPttIdleTimer();
  setStatus(reasonText || "تم الإنهاء");
  setMicEnabled(false);
  if (!DISABLE_TRANSCRIPT && state.assistantBuffer) {
    pushTranscript("assistant", state.assistantBuffer);
    state.assistantBuffer = "";
  }
  const summaryPayload = buildSummaryPayload();
  state.summaryPayload = summaryPayload;
  state.finalizeContext = {
    summaryPayload: summaryPayload,
    sessionMeta: state.sessionMeta ? { ...state.sessionMeta } : null,
    startedAt: state.startedAt,
    summaryId: state.summaryId,
    sessionKey: state.sessionKey,
    clientCallId: state.clientCallId,
  };
  if (state.recorder && state.recorder.state !== "inactive") {
    try {
      state.recorder.stop();
    } catch (err) {
      console.warn("Recorder stop failed:", err);
    }
  }
  try {
    clearRecorderFlushTimer();
    if (state.dc) state.dc.close();
    if (state.pc) state.pc.close();
    if (state.micStream) state.micStream.getTracks().forEach((t) => t.stop());
    if (state.audioEl) state.audioEl.remove();
  } catch (e) {
    console.warn("Error during stopAgent:", e);
  }

  state.running = false;
  state.pc = null;
  state.dc = null;
  state.micStream = null;
  state.audioEl = null;
  state.pendingText = null;
  state.responseInFlight = false;
  state.micEnabled = false;
  state.pttLastActivityAt = 0;
  state.startedAt = null;
  state.sessionMeta = null;
  state.remoteStream = null;
  state.unloadHandled = false;
  showCallActions(false);
  togglePanel(false);
  updatePushToTalkButton();

  if (!state.recorder && summaryPayload) {
    const endCtx = { ...(state.finalizeContext || {}) };
    submitSummary(summaryPayload);
    endSession(endCtx).finally(() => {
      clearFinalizeState();
    });
    return;
  }
  if (!state.recorder) {
    const endCtx = { ...(state.finalizeContext || {}) };
    endSession(endCtx).finally(() => {
      clearFinalizeState();
    });
  }
}

function wireLifecycleGuards() {
  if (state.lifecycleWired) return;
  state.lifecycleWired = true;
  window.addEventListener("pagehide", handlePageUnload);
  window.addEventListener("beforeunload", handlePageUnload);
}

function wireUI() {
  if (window[UI_WIRED_KEY]) return;
  window[UI_WIRED_KEY] = true;
  wireLifecycleGuards();
  const fab = qs("oai-agent-fab");
  const startBtn = qs("oai-agent-start");
  const stopBtn = qs("oai-agent-stop");
  const pttBtn = qs("oai-agent-ptt");
  const mobileInput = qs("oai-agent-mobile-input");

  if (fab) {
    fab.addEventListener("click", async (ev) => {
      ev.preventDefault();
      try {
        if (!state.running) {
          togglePanel(true);
          prepareMobileStep();
        } else {
          stopAgent();
        }
      } catch (e) {
        console.error("Agent error:", e);
        setStatus("تعذر الاتصال: " + errorToText(e));
        state.running = false;
      }
    });
  }

  if (startBtn) {
    startBtn.addEventListener("click", async (ev) => {
      ev.preventDefault();
      if (state.running) return;
      try {
        await startAgentFromPanel();
      } catch (e) {
        console.error("Agent error:", e);
        setStatus("تعذر الاتصال: " + errorToText(e));
        state.running = false;
        showMobileStep(true);
        showCallActions(false);
      }
    });
  }

  if (mobileInput) {
    mobileInput.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") {
        ev.preventDefault();
        const btn = qs("oai-agent-start");
        if (btn) btn.click();
      }
    });
  }

  if (stopBtn) {
    stopBtn.addEventListener("click", (ev) => {
      ev.preventDefault();
      stopAgent();
    });
  }

  if (pttBtn) {
    pttBtn.addEventListener("pointerdown", startPushToTalk);
    pttBtn.addEventListener("pointerup", stopPushToTalk);
    pttBtn.addEventListener("pointerleave", stopPushToTalk);
    pttBtn.addEventListener("pointercancel", stopPushToTalk);
    pttBtn.addEventListener("blur", stopPushToTalk);
    pttBtn.addEventListener("keydown", (ev) => {
      if (ev.key === " " || ev.key === "Enter") startPushToTalk(ev);
    });
    pttBtn.addEventListener("keyup", (ev) => {
      if (ev.key === " " || ev.key === "Enter") stopPushToTalk(ev);
    });
    window.addEventListener("pointerup", stopPushToTalk);
    window.addEventListener("pointercancel", stopPushToTalk);
    updatePushToTalkButton();
  }

}

if (document.readyState === "complete" || document.readyState === "interactive") {
  wireUI();
  notifyEmbedHost();
} else {
  document.addEventListener("DOMContentLoaded", () => {
    wireUI();
    notifyEmbedHost();
  });
}

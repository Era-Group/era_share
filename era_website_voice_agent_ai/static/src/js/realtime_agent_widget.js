/** @odoo-module **/

// Disable transcript processing to speed up voice conversations
const DISABLE_TRANSCRIPT = true;

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
  return {
    promptId: w?.dataset?.promptId || "",
    model: w?.dataset?.model || "gpt-realtime-mini",
    voice: voice,
    callerPhone: w?.dataset?.callerPhone || "",
    callerCompany: w?.dataset?.callerCompany || "",
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
  } finally {
    state.starting = false;
    setStartButtonLoading(false);
  }
}

function setStatus(text) {
  const el = qs("oai-agent-status");
  if (el) el.textContent = text;
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
  return json.result || json;
}

function safeSend(obj) {
  if (!state.dc || state.dc.readyState !== "open") return false;
  state.dc.send(JSON.stringify(obj));
  return true;
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

  pushTranscript("user", trimmed);

  safeSend({
    type: "conversation.item.create",
    item: {
      type: "message",
      role: "user",
      content: [{ type: "input_text", text: trimmed }],
    },
  });

  safeSend({
    type: "response.create",
    response: {
      modalities: ["audio", "text"],
    },
  });
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
  const tok = await rpcJson("/realtime_agent/token", {});
  const promptFallback = !!tok?.prompt_fallback;
  state.sessionMeta.promptFallback = promptFallback;
  
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

  try {
    const session = await rpcJson("/realtime_agent/session_start", {
      prompt_id: cfg.promptId,
      model: cfg.model,
      voice: cfg.voice,
      caller_phone: cfg.callerPhone,
      caller_company: cfg.callerCompany,
      client_call_id: state.clientCallId,
    });
    if (session && !session.error) {
      state.summaryId = session.summary_id || null;
      state.sessionKey = session.session_key || "";
    } else {
      console.warn("Session start failed:", session?.error || "unknown");
    }
  } catch (err) {
    console.warn("Session start request failed:", err);
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
  micStream.getTracks().forEach((t) => pc.addTrack(t, micStream));
  initRecorder(micStream);

  const dc = pc.createDataChannel("oai-events");
  dc.addEventListener("message", (e) => {
    try {
      const evt = JSON.parse(e.data);
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
        console.error("OpenAI Error:", evt.error);
        setStatus("صار خطأ في الاتصال");
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
    setStatus("متصل ✅");

    const sessionUpdate = {
      model: cfg.model,
      voice: cfg.voice,
      input_audio_transcription: {
        model: "gpt-4o-mini-transcribe",
      },
    };
    if (cfg.promptId && !state.sessionMeta?.promptFallback) {
      sessionUpdate.prompt = { id: cfg.promptId };
    }

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
  state.micStream = micStream;
  state.audioEl = audioEl;
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

async function submitSummaryAudio(payload) {
  if (!payload || !payload.audio_base64) return;
  try {
    const res = await rpcJson("/realtime_agent/summary_audio", payload);
    if (res?.error) {
      console.error("Summary audio error:", res.error, res.details || "");
      return;
    }
    if (res?.warning) {
      console.warn("Summary audio warning:", res.warning);
    }
    if (res?.id && !state.summaryId) state.summaryId = res.id;
  } catch (err) {
    console.error("Summary audio request failed:", err);
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
  if (state.recordedChunks.length === 0) {
    state.recorder = null;
    if (summaryPayload) {
      submitSummary(summaryPayload);
      state.summaryPayload = null;
    }
    return;
  }
  state.recordingFinalized = true;
  try {
    const blob = new Blob(state.recordedChunks, { type: state.recorder.mimeType || "audio/webm" });
    const base64 = await blobToBase64(blob);
    if (!base64) return;
    const durationSeconds = startedAt ? Math.round((Date.now() - startedAt) / 1000) : null;
    const fallbackTranscript = summaryPayload?.transcript || "";
    const payload = {
      audio_base64: base64,
      audio_mimetype: blob.type || "audio/webm",
      audio_filename: "realtime-call.webm",
      transcript: fallbackTranscript,
      prompt_id: sessionMeta?.promptId || "",
      model: sessionMeta?.model || "",
      voice: sessionMeta?.voice || "",
      caller_phone: sessionMeta?.callerPhone || "",
      caller_company: sessionMeta?.callerCompany || "",
      duration_seconds: durationSeconds,
      summary_id: summaryId,
      session_key: sessionKey,
      client_call_id: clientCallId,
    };
    await submitSummaryAudio(payload);
  } catch (err) {
    console.warn("Recording finalize failed:", err);
  } finally {
    clearRecorderFlushTimer();
    state.recorder = null;
    state.recordedChunks = [];
    state.summaryPayload = null;
    state.finalizeContext = null;
    state.summaryId = null;
    state.sessionKey = "";
    state.clientCallId = "";
    state.stopping = false;
    if (state.audioContext) {
      state.audioContext.close().catch(() => {});
      state.audioContext = null;
      state.mixerDest = null;
    }
  }
}

function submitSummaryAudioBeacon(blob, ctx = null) {
  if (!blob || blob.size === 0) return false;
  const context = ctx || {};
  const sessionMeta = context.sessionMeta || state.sessionMeta || {};
  const summaryPayload = context.summaryPayload || state.summaryPayload || {};
  const startedAt = context.startedAt || state.startedAt;
  const summaryId = context.summaryId || state.summaryId || "";
  const sessionKey = context.sessionKey || state.sessionKey || "";
  const clientCallId = context.clientCallId || state.clientCallId || "";
  const durationSeconds = startedAt ? Math.round((Date.now() - startedAt) / 1000) : "";
  const fallbackTranscript = summaryPayload?.transcript || "";

  const formData = new FormData();
  formData.append("audio_file", blob, "realtime-call.webm");
  formData.append("audio_filename", "realtime-call.webm");
  formData.append("audio_mimetype", blob.type || "audio/webm");
  formData.append("transcript", fallbackTranscript);
  formData.append("prompt_id", sessionMeta?.promptId || "");
  formData.append("model", sessionMeta?.model || "");
  formData.append("voice", sessionMeta?.voice || "");
  formData.append("caller_phone", sessionMeta?.callerPhone || "");
  formData.append("caller_company", sessionMeta?.callerCompany || "");
  formData.append("duration_seconds", durationSeconds ? String(durationSeconds) : "");
  formData.append("summary_id", String(summaryId || ""));
  formData.append("session_key", sessionKey || "");
  formData.append("client_call_id", clientCallId || "");

  let sent = false;
  if (navigator.sendBeacon) {
    try {
      sent = navigator.sendBeacon("/realtime_agent/summary_audio_beacon", formData);
    } catch (err) {
      console.warn("Summary audio beacon failed:", err);
    }
  }
  if (!sent) {
    fetch("/realtime_agent/summary_audio_beacon", {
      method: "POST",
      body: formData,
      keepalive: true,
      credentials: "same-origin",
    }).catch((err) => console.warn("Summary audio keepalive fallback failed:", err));
  }
  return sent;
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

  if (state.recordedChunks.length > 0) {
    state.recordingFinalized = true;
    const mimeType = state.recorder?.mimeType || "audio/webm";
    const blob = new Blob(state.recordedChunks, { type: mimeType });
    submitSummaryAudioBeacon(blob, snapshot);
    return;
  }

}

function stopAgent() {
  if (state.stopping) return;
  state.stopping = true;
  setStatus("تم الإنهاء");
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
  state.startedAt = null;
  state.sessionMeta = null;
  state.remoteStream = null;
  state.unloadHandled = false;
  showCallActions(false);
  togglePanel(false);

  if (!state.recorder && summaryPayload) {
    submitSummary(summaryPayload);
    state.summaryPayload = null;
  }
}

function wireLifecycleGuards() {
  if (state.lifecycleWired) return;
  state.lifecycleWired = true;
  window.addEventListener("pagehide", handlePageUnload);
  window.addEventListener("beforeunload", handlePageUnload);
}

function wireUI() {
  wireLifecycleGuards();
  const fab = qs("oai-agent-fab");
  const startBtn = qs("oai-agent-start");
  const stopBtn = qs("oai-agent-stop");
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
        setStatus("تعذر الاتصال: " + e.message);
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
        setStatus("تعذر الاتصال: " + e.message);
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

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
};

function qs(id) {
  const el = document.getElementById(id);
  if (!el) console.warn(`Element #${id} not found`);
  return el;
}

function widgetEl() {
  return document.getElementById("oai-agent-widget");
}

function getConfig() {
  const w = widgetEl();
  const rawVoice = w?.dataset?.voice || "";
  const voice = (!rawVoice || rawVoice === "marin") ? "marin" : rawVoice;
  return {
    promptId: w?.dataset?.promptId || "",
    model: w?.dataset?.model || "gpt-realtime",
    voice: voice,
    callerPhone: w?.dataset?.callerPhone || "",
    callerCompany: w?.dataset?.callerCompany || "",
  };
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
}

async function rpcJson(url, params = {}) {
  // Odoo JSON-RPC expects a specific wrapper
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
  const json = await res.json();
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
      if (e.data && e.data.size > 0) state.recordedChunks.push(e.data);
    };
    recorder.onstop = () => {
      finalizeRecording();
    };
    state.recorder = recorder;
    recorder.start(1000);
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
    }, 1000);
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

  // 1) create a user message item
  safeSend({
    type: "conversation.item.create",
    item: {
      type: "message",
      role: "user",
      content: [{ type: "input_text", text: trimmed }],
    },
  });

  // 2) ask model to respond (audio)
  safeSend({
    type: "response.create",
    response: {
      modalities: ["audio", "text"],
    },
  });
}

async function startAgent() {
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
  state.finalizeContext = null;
  state.transcript = [];
  state.assistantBuffer = "";
  if (!DISABLE_TRANSCRIPT) {
    const transcriptEl = qs("oai-agent-transcript");
    if (transcriptEl) transcriptEl.innerHTML = "";
  }
  if (!cfg.promptId) {
    throw new Error("Missing prompt id (openai.realtime_prompt_id). Configure it in Settings → OpenAI.");
  }

  // 1) get ephemeral token from Odoo
  setStatus("جاري التجهيز...");
  const tok = await rpcJson("/realtime_agent/token", {});
  
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

  // 2) WebRTC PeerConnection
  const pc = new RTCPeerConnection();

  // audio output element (model voice)
  const audioEl = document.createElement("audio");
  audioEl.autoplay = true;
  audioEl.className = "oai-agent-audio";
  document.body.appendChild(audioEl);

  pc.ontrack = (e) => {
    console.log("Received remote track", e.streams[0]);
    audioEl.srcObject = e.streams[0];
    state.remoteStream = e.streams[0];
    attachRemoteToMixer(state.remoteStream);
    // Ensure audio plays
    audioEl.play().catch(err => console.error("Audio play failed:", err));
  };

  // microphone
  console.log("Requesting microphone...");
  const micStream = await navigator.mediaDevices.getUserMedia({ audio: true });
  console.log("Microphone acquired");
  micStream.getTracks().forEach((t) => pc.addTrack(t, micStream));
  initRecorder(micStream);

  // data channel for events
  const dc = pc.createDataChannel("oai-events");
  dc.addEventListener("message", (e) => {
    try {
      const evt = JSON.parse(e.data);
      console.log("DC Event:", evt.type, evt);
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

  // 3) Offer/Answer via OpenAI Realtime calls endpoint
  const offer = await pc.createOffer();
  await pc.setLocalDescription(offer);

  console.log(`Sending SDP offer to OpenAI (Model: ${cfg.model})...`);
  // Ensure the model is correct in the URL; OpenAI is sensitive to this.
  // Force a known valid model for the SDP URL if it's the generic placeholder
  const modelUrl = cfg.model || "gpt-realtime";
  
  // LOG THE SDP TO SEE IF IT IS VALID
  console.log("Offer SDP:", offer.sdp);
  
  console.log(`Debug - EPHEMERAL_KEY exists: ${!!EPHEMERAL_KEY}`);
  console.log(`Debug - modelUrl used: ${modelUrl}`);

  // THE FIX: OpenAI requires the Authorization header to be exactly 'Bearer ' + token.
  console.log("Using Token:", EPHEMERAL_KEY.substring(0, 10) + "...");
  // OpenAI Realtime Beta endpoint: https://api.openai.com/v1/realtime/calls
  const sdpResp = await fetch("https://api.openai.com/v1/realtime/calls?model=" + modelUrl, {
    method: "POST",
    body: offer.sdp,
    headers: {
      "Authorization": `Bearer ${EPHEMERAL_KEY.trim()}`,
      "Content-Type": "application/sdp",
      "OpenAI-Beta": "realtime=v1",
    },
  });
  
  // LOG RESPONSE FOR DEBUG
  if (sdpResp.status === 400) {
    const raw = await sdpResp.clone().text();
    console.warn("400 Error Raw Content:", raw);
  }
  
  // LOG FULL RESPONSE OBJECT
  console.log("SDP Response status:", sdpResp.status);
  console.log("SDP Response headers:", [...sdpResp.headers.entries()]);

  if (!sdpResp.ok) {
    const errorText = await sdpResp.text();
    console.error("OpenAI SDP Error Raw:", errorText);
    try {
      const errorJson = JSON.parse(errorText);
      console.error("OpenAI SDP Error JSON:", errorJson);
    } catch (e) {}
    throw new Error(`Failed to create Realtime call: ${sdpResp.status} ${sdpResp.statusText}`);
  }

  const answerSdp = await sdpResp.text();
  await pc.setRemoteDescription({ type: "answer", sdp: answerSdp });

  // 4) Once data channel is open, bind your published prompt (agent)
  dc.addEventListener("open", async () => {
    console.log("Data channel opened");
    setStatus("متصل ✅");

    safeSend({
      type: "session.update",
      session: {
        model: cfg.model,
        prompt: { id: cfg.promptId },
        voice: cfg.voice,
        input_audio_transcription: {
          model: "gpt-4o-mini-transcribe",
        },
      },
    });

    // If user typed before connect, send it now
    if (state.pendingText) {
      sendUserText(state.pendingText);
      state.pendingText = null;
    }
  });

  // Handle connection closure
  pc.addEventListener("connectionstatechange", () => {
    console.log("Connection state:", pc.connectionState);
    if (pc.connectionState === "failed" || pc.connectionState === "disconnected") {
      setStatus("انقطع الاتصال");
      stopAgent();
    }
  });

  state.running = true;
  console.log("Agent started successfully");
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
    console.log("Summary saved:", res);
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
    console.log("Summary audio saved:", res);
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
  };
}

async function finalizeRecording() {
  if (state.recordingFinalized) return;
  if (!state.recorder) return;
  const ctx = state.finalizeContext || {};
  const sessionMeta = ctx.sessionMeta || state.sessionMeta || {};
  const summaryPayload = ctx.summaryPayload || state.summaryPayload || null;
  const startedAt = ctx.startedAt || state.startedAt;
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
    const base64 = await new Promise((resolve) => {
      const reader = new FileReader();
      reader.onloadend = () => {
        const dataUrl = reader.result || "";
        const parts = String(dataUrl).split(",");
        resolve(parts.length > 1 ? parts[1] : "");
      };
      reader.readAsDataURL(blob);
    });
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
  };

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

  if (summaryPayload?.transcript) {
    sendJsonRpcBeacon("/realtime_agent/session_abandoned", {
      transcript: summaryPayload.transcript || "",
      prompt_id: snapshot.sessionMeta?.promptId || "",
      model: snapshot.sessionMeta?.model || "",
      voice: snapshot.sessionMeta?.voice || "",
      caller_phone: snapshot.sessionMeta?.callerPhone || "",
      caller_company: snapshot.sessionMeta?.callerCompany || "",
      duration_seconds: summaryPayload.duration_seconds || "",
    });
    return;
  }

  sendJsonRpcBeacon("/realtime_agent/session_abandoned", {
    prompt_id: snapshot.sessionMeta?.promptId || "",
    model: snapshot.sessionMeta?.model || "",
    voice: snapshot.sessionMeta?.voice || "",
    caller_phone: snapshot.sessionMeta?.callerPhone || "",
    caller_company: snapshot.sessionMeta?.callerCompany || "",
    duration_seconds: snapshot.startedAt ? Math.round((Date.now() - snapshot.startedAt) / 1000) : "",
  });
}

function stopAgent() {
  console.log("Stopping agent...");
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

  // We don't togglePanel(false) immediately if we want to show the "تم الإنهاء" status
  setTimeout(() => {
    if (!state.running) togglePanel(false);
  }, 2000);

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
  const stopBtn = qs("oai-agent-stop");

  if (fab) {
    fab.addEventListener("click", async (ev) => {
      ev.preventDefault();
      try {
        if (!state.running) {
          togglePanel(true);
          setStatus("جاري الاتصال...");
          await startAgent();
        } else {
          stopAgent();
        }
      } catch (e) {
        console.error("Agent error:", e);
        setStatus("تعذر الاتصال: " + e.message);
        // Don't stopAgent immediately if it failed to even start
        state.running = false;
        // togglePanel(false); // Maybe keep panel open to show error?
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

/**
 * Odoo 19 uses a modern JS framework.
 * While we could use PublicWidget, for a simple global floating button
 * we can just ensure wireUI runs even if the script loads after DOMContentLoaded.
 */
if (document.readyState === "complete" || document.readyState === "interactive") {
  wireUI();
} else {
  document.addEventListener("DOMContentLoaded", () => {
    wireUI();
  });
}

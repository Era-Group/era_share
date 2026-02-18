/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { UserAgent } from "@voip/core/user_agent_service";

const originalOnSessionStateChange = UserAgent.prototype._onSessionStateChange;
const originalHangup = UserAgent.prototype.hangup;
const originalRejectIncomingCall = UserAgent.prototype.rejectIncomingCall;
const originalOnReferAccepted = UserAgent.prototype._onReferAccepted;

function pickMimeType() {
  const candidates = [
    "audio/webm;codecs=opus",
    "audio/webm",
    "audio/ogg;codecs=opus",
    "audio/ogg",
  ];
  for (const candidate of candidates) {
    if (MediaRecorder.isTypeSupported(candidate)) return candidate;
  }
  return "";
}

function toBase64(blob) {
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
  const json = await res.json();
  return json.result || json;
}

patch(UserAgent.prototype, {
  _oaiGetRemoteStream() {
    if (this.remoteAudio?.srcObject instanceof MediaStream) {
      return this.remoteAudio.srcObject;
    }
    const remoteStream = new MediaStream();
    const pc = this.session?.sipSession?.sessionDescriptionHandler?.peerConnection;
    if (!pc) return null;
    for (const receiver of pc.getReceivers()) {
      if (receiver.track) remoteStream.addTrack(receiver.track);
    }
    return remoteStream.getTracks().length ? remoteStream : null;
  },

  _oaiGetLocalStream() {
    const pc = this.session?.sipSession?.sessionDescriptionHandler?.peerConnection;
    if (!pc) return null;
    const localStream = new MediaStream();
    for (const sender of pc.getSenders()) {
      if (sender.track) localStream.addTrack(sender.track);
    }
    return localStream.getTracks().length ? localStream : null;
  },

  _oaiStartRecording() {
    if (this._oaiRecording?.active || !this.session?.call) return;
    const localStream = this._oaiGetLocalStream();
    const remoteStream = this._oaiGetRemoteStream();
    if (!localStream && !remoteStream) return;

    try {
      const audioContext = new (window.AudioContext || window.webkitAudioContext)();
      const dest = audioContext.createMediaStreamDestination();
      if (localStream) {
        const localSource = audioContext.createMediaStreamSource(localStream);
        localSource.connect(dest);
      }
      if (remoteStream) {
        const remoteSource = audioContext.createMediaStreamSource(remoteStream);
        remoteSource.connect(dest);
      }
      const mimeType = pickMimeType();
      const recorder = new MediaRecorder(dest.stream, mimeType ? { mimeType } : undefined);
      const chunks = [];
      recorder.ondataavailable = (e) => {
        if (e.data && e.data.size > 0) chunks.push(e.data);
      };
      recorder.onstop = () => this._oaiFinalizeRecording();
      recorder.start(1000);
      const call = this.session.call;
      this._oaiRecording = {
        active: true,
        audioContext,
        recorder,
        chunks,
        startedAt: Date.now(),
        callSnapshot: {
          id: call.id,
          phoneNumber: call.phoneNumber,
          partnerName: call.partner?.name || "",
          direction: call.direction,
        },
      };
    } catch (err) {
      console.warn("VoIP recording start failed:", err);
      this._oaiRecording = null;
    }
  },

  _oaiStopRecording() {
    if (!this._oaiRecording?.active) return;
    const recorder = this._oaiRecording.recorder;
    this._oaiRecording.active = false;
    if (recorder && recorder.state !== "inactive") {
      try {
        recorder.stop();
      } catch (err) {
        console.warn("VoIP recording stop failed:", err);
        this._oaiCleanupRecording();
      }
    } else {
      this._oaiCleanupRecording();
    }
  },

  async _oaiFinalizeRecording() {
    const recording = this._oaiRecording;
    if (!recording) return;
    try {
      const blob = new Blob(recording.chunks || [], {
        type: recording.recorder?.mimeType || "audio/webm",
      });
      if (!blob.size) return;
      const base64 = await toBase64(blob);
      if (!base64) return;

      const call = recording.callSnapshot || {};
      const durationSeconds = recording.startedAt
        ? Math.round((Date.now() - recording.startedAt) / 1000)
        : null;
      const normalizedCallSource = ["outgoing", "outbound", "out"].includes(call.direction)
        ? "outgoing"
        : ["incoming", "incomming", "inbound", "in"].includes(call.direction)
        ? "incoming"
        : "incoming";
      const payload = {
        audio_base64: base64,
        audio_mimetype: blob.type || "audio/webm",
        audio_filename: call.id ? `voip-call-${call.id}.webm` : "voip-call.webm",
        duration_seconds: durationSeconds,
        caller_phone: call.phoneNumber || "",
        caller_company: call.partnerName || "",
        direction: call.direction || "",
        call_source: normalizedCallSource,
        operator_extension: this.voip?.store?.settings?.voip_username || "",
        call_id: call.id ? `voip:${call.id}` : "",
      };
      const res = await rpcJson("/realtime_agent/voip/recording", payload);
      if (res?.error) {
        console.error("VoIP recording summary error:", res.error, res.details || "");
      }
      if (res?.warning) {
        console.warn("VoIP recording summary warning:", res.warning);
      }
    } catch (err) {
      console.warn("VoIP recording finalize failed:", err);
    } finally {
      this._oaiCleanupRecording();
    }
  },

  _oaiCleanupRecording() {
    const recording = this._oaiRecording;
    this._oaiRecording = null;
    if (recording?.audioContext) {
      recording.audioContext.close().catch(() => {});
    }
  },

  _onSessionStateChange(newState) {
    if (typeof originalOnSessionStateChange === "function") {
      originalOnSessionStateChange.call(this, newState);
    }
    if (window.SIP?.SessionState?.Established && newState === window.SIP.SessionState.Established) {
      this._oaiStartRecording();
    }
    if (window.SIP?.SessionState?.Terminated && newState === window.SIP.SessionState.Terminated) {
      this._oaiStopRecording();
    }
  },

  hangup(...args) {
    this._oaiStopRecording();
    return typeof originalHangup === "function" ? originalHangup.apply(this, args) : undefined;
  },

  rejectIncomingCall(...args) {
    this._oaiStopRecording();
    return typeof originalRejectIncomingCall === "function"
      ? originalRejectIncomingCall.apply(this, args)
      : undefined;
  },

  _onReferAccepted(response) {
    this._oaiStopRecording();
    return typeof originalOnReferAccepted === "function"
      ? originalOnReferAccepted.call(this, response)
      : undefined;
  },
});

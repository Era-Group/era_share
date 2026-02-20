(function () {
  const LOADER_STATE_KEY = "__eraRealtimeEmbedLoaderState";

  function currentScript() {
    if (document.currentScript) return document.currentScript;
    const scripts = document.getElementsByTagName("script");
    return scripts[scripts.length - 1];
  }

  function toInt(value, fallback) {
    const num = parseInt(value, 10);
    return Number.isFinite(num) ? num : fallback;
  }

  function normalizeBaseUrl(scriptEl) {
    const dataUrl = (scriptEl.dataset.baseUrl || "").trim();
    if (dataUrl) return dataUrl.replace(/\/+$/, "");
    const src = scriptEl.src || "";
    try {
      return new URL(src, window.location.href).origin;
    } catch (_err) {
      return "";
    }
  }

  const scriptEl = currentScript();
  if (!scriptEl) return;

  const baseUrl = normalizeBaseUrl(scriptEl);
  if (!baseUrl) return;

  const loaderState = window[LOADER_STATE_KEY] || { initialized: false };
  if (loaderState.initialized) return;
  loaderState.initialized = true;
  window[LOADER_STATE_KEY] = loaderState;

  const params = new URLSearchParams();
  const promptId = (scriptEl.dataset.promptId || "").trim();
  const model = (scriptEl.dataset.model || "").trim();
  const voice = (scriptEl.dataset.voice || "").trim();
  const label = (scriptEl.dataset.label || "").trim();
  const callerCompany = (scriptEl.dataset.callerCompany || "").trim();
  if (promptId) params.set("prompt_id", promptId);
  if (model) params.set("model", model);
  if (voice) params.set("voice", voice);
  if (label) params.set("widget_label", label);
  if (callerCompany) params.set("caller_company", callerCompany);

  const frameSrc = `${baseUrl}/realtime_agent/embed/frame${params.toString() ? `?${params.toString()}` : ""}`;
  const zIndex = toInt(scriptEl.dataset.zIndex, 2147483000);
  const right = toInt(scriptEl.dataset.right, 14);
  const bottom = toInt(scriptEl.dataset.bottom, 14);
  const closedWidth = toInt(scriptEl.dataset.closedWidth, 92);
  const closedHeight = toInt(scriptEl.dataset.closedHeight, 92);

  // Avoid duplicate widget if embed script is injected more than once.
  const existing = document.getElementById("era-realtime-agent-embed-frame");
  if (existing) return;

  const frame = document.createElement("iframe");
  frame.id = "era-realtime-agent-embed-frame";
  frame.src = frameSrc;
  frame.title = "ERA Realtime Voice Agent";
  frame.allow = "microphone; autoplay; clipboard-read; clipboard-write";
  frame.setAttribute("aria-hidden", "false");
  frame.style.position = "fixed";
  frame.style.right = `${right}px`;
  frame.style.bottom = `${bottom}px`;
  frame.style.width = `${closedWidth}px`;
  frame.style.height = `${closedHeight}px`;
  frame.style.border = "0";
  frame.style.background = "transparent";
  frame.style.overflow = "hidden";
  frame.style.zIndex = String(zIndex);

  window.addEventListener("message", function (event) {
    if (event.origin !== new URL(baseUrl).origin) return;
    const data = event.data || {};
    if (data.source !== "era-realtime-widget" || data.type !== "resize") return;
    const width = toInt(data.width, closedWidth);
    const height = toInt(data.height, closedHeight);
    frame.style.width = `${Math.max(80, width)}px`;
    frame.style.height = `${Math.max(80, height)}px`;
  });

  if (document.body) {
    if (!document.getElementById(frame.id)) {
      document.body.appendChild(frame);
    }
  } else {
    window.addEventListener(
      "DOMContentLoaded",
      function () {
        if (!document.getElementById(frame.id)) {
          document.body.appendChild(frame);
        }
      },
      { once: true }
    );
  }
})();

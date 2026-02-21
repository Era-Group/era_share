/** @odoo-module **/

const TARGET_SELECTOR = "textarea[name='openai_realtime_system_instructions_cfg']";
const MIN_HEIGHT = 160;

function resizeTextarea(el) {
  if (!el) return;
  el.style.height = "auto";
  const nextHeight = Math.max(el.scrollHeight || 0, MIN_HEIGHT);
  el.style.height = `${nextHeight}px`;
}

function bindTextarea(el) {
  if (!el || el.dataset.eraAutoHeightBound === "1") return;
  el.dataset.eraAutoHeightBound = "1";
  resizeTextarea(el);
  el.addEventListener("input", () => resizeTextarea(el));
}

function scanAndBind(root = document) {
  if (!root || !root.querySelectorAll) return;
  root.querySelectorAll(TARGET_SELECTOR).forEach((el) => bindTextarea(el));
}

function startObserver() {
  const observer = new MutationObserver((mutations) => {
    for (const mutation of mutations) {
      for (const added of mutation.addedNodes) {
        if (!(added instanceof Element)) continue;
        if (added.matches?.(TARGET_SELECTOR)) {
          bindTextarea(added);
        }
        if (added.querySelectorAll) {
          scanAndBind(added);
        }
      }
    }
  });
  observer.observe(document.body, { childList: true, subtree: true });
}

function boot() {
  scanAndBind(document);
  startObserver();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", boot);
} else {
  boot();
}

/*
 * Fatoratec livechat — two runtime fallbacks the SCSS can't cover:
 *   1. Hide the "Ask Human" button when class-based CSS misses it (Odoo
 *      sometimes renders it as a plain <button class="btn btn-primary">
 *      اسأل بشرياً</button> with no stable hook).
 *   2. Force the livechat widget to use the website's body font. Odoo's
 *      .o-mail-* CSS pins a font-family with high specificity that beats
 *      our stylesheet — but inline style with !important always wins, so
 *      we read getComputedStyle(document.body).fontFamily and stamp it.
 *
 * Pairs with livechat_overrides.scss.
 */
(function () {
    "use strict";

    const PHRASES = [
        "اسأل بشرياً",
        "اسأل بشريا",
        "اسأل بشرى",
        "بشرياً",
        "Ask Human",
        "Ask a Human",
        "Request human",
        "Talk to a human",
    ];

    // Selectors that match every node that may carry a font-family declaration
    // inside the livechat widget. Includes the modern Owl mail classes and
    // the older thread-window fallback templates.
    const CHAT_ROOTS = [
        ".o-livechat-root",
        ".o-livechat-Conversation",
        ".o-mail-Thread",
        ".o_livechat_window",
        ".o_thread_window",
    ];

    let cachedFont = null;
    function bodyFont() {
        if (cachedFont) return cachedFont;
        try {
            const f = getComputedStyle(document.body).fontFamily;
            if (f && f.trim()) cachedFont = f;
        } catch (e) { /* body not ready yet */ }
        return cachedFont;
    }

    function shouldHide(el) {
        if (!el || !el.textContent) return false;
        const txt = el.textContent.trim();
        if (txt.length > 60) return false; // buttons are short; skip paragraphs
        return PHRASES.some((p) => txt.includes(p));
    }

    function hide(el) {
        el.classList.add("era-hide-ask-human");
        el.style.cssText = "display:none !important; visibility:hidden !important;";
        // If the parent only wraps this one control, hide it too — keeps the
        // surrounding layout from leaving an empty padded box.
        const parent = el.parentElement;
        if (parent && parent.children.length === 1) {
            parent.classList.add("era-hide-ask-human");
        }
    }

    function stampFont(root) {
        const font = bodyFont();
        if (!font) return;
        CHAT_ROOTS.forEach((sel) => {
            root.querySelectorAll(sel).forEach((container) => {
                // Stamp the container plus every descendant — input/textarea/
                // button included, since each has its own UA default.
                container.style.setProperty("font-family", font, "important");
                container.querySelectorAll("*").forEach((el) => {
                    el.style.setProperty("font-family", font, "important");
                });
            });
        });
    }

    function scan(root) {
        if (!root || !root.querySelectorAll) return;
        root.querySelectorAll("button, a, .btn").forEach((el) => {
            if (shouldHide(el)) hide(el);
        });
        // Walk shadow roots — livechat embeds use them in some themes.
        root.querySelectorAll("*").forEach((el) => {
            if (el.shadowRoot) scan(el.shadowRoot);
        });
    }

    function nuke() {
        scan(document);
        stampFont(document);
    }

    // Initial sweep + short polling burst to catch lazy-mounted widgets, then
    // hand off to a permanent MutationObserver for late re-renders.
    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", nuke);
    } else {
        nuke();
    }
    let ticks = 0;
    const poll = setInterval(() => {
        nuke();
        if (++ticks > 120) clearInterval(poll); // 30s @ 250ms
    }, 250);

    new MutationObserver(nuke).observe(document.documentElement, {
        childList: true,
        subtree: true,
        characterData: true,
    });
})();

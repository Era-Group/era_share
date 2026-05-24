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

    // Belt to the SCSS braces for the horizontal-scroll bug: stamp overflow
    // + wrap rules inline so they win over Odoo's .o-mail-* declarations.
    // Every container gets overflow-x:hidden; every bubble/body/content node
    // gets max-width:100% + overflow-wrap:anywhere so long Arabic strings
    // wrap inside the bubble instead of dragging the panel sideways.
    const BUBBLE_SELECTORS = [
        ".o-mail-Message",
        ".o-mail-Message-bubble",
        ".o-mail-Message-body",
        ".o-mail-Message-content",
        ".o-mail-Message-textContent",
        ".o-mail-Message-core",
        ".o-mail-Message > *",
    ];

    // Walk up from a chat message until we hit a position:fixed/absolute
    // element — that's almost certainly the chat-popup root. Caches the
    // result so we don't recompute on every MutationObserver tick.
    let cachedPanel = null;
    function findChatPanel(root) {
        if (cachedPanel && document.body.contains(cachedPanel)) return cachedPanel;
        const msg = root.querySelector(".o-mail-Message");
        if (!msg) return null;
        let el = msg;
        while (el && el !== document.body) {
            const cs = getComputedStyle(el);
            if (cs.position === "fixed" || cs.position === "absolute") {
                cachedPanel = el;
                return el;
            }
            el = el.parentElement;
        }
        return null;
    }

    function stampNoOverflow(root) {
        // 1. Strip h-100 + overflow-auto from the user-identified inner
        //    thread scroller. Without this, the scroller pins itself to
        //    100% of the chat panel height and its auto-overflow shows
        //    a vertical scrollbar inside the chat whenever bubble content
        //    barely exceeds the calculated row height (the taller line
        //    height of a custom website font triggers this every time).
        root.querySelectorAll(
            ".d-flex.flex-column.h-100.overflow-auto.o-scrollbar-thin"
        ).forEach((el) => {
            el.style.setProperty("height", "auto", "important");
            el.style.setProperty("max-height", "none", "important");
            el.style.setProperty("overflow", "visible", "important");
            el.style.setProperty("overflow-y", "visible", "important");
        });

        // 2. Clip the outermost chat panel horizontally so any inner
        //    flex child with intrinsic width > panel width can't drag
        //    the whole panel sideways. The panel root is detected by
        //    walking up from a message until we find a positioned ancestor.
        const panel = findChatPanel(root);
        if (panel) {
            panel.style.setProperty("overflow-x", "hidden", "important");
            panel.style.setProperty("max-width", "100vw", "important");
        }

        // 3. Bubble text content — wrap long Arabic strings, never grow
        //    a min-content shoulder past the column. `min-width: 0` is
        //    the standard flexbox-overflow remedy (flex items default to
        //    min-width: auto = min-content, which prevents shrinking
        //    below their longest word). overflow: visible removes the
        //    per-bubble scrollbar Odoo paints whenever content barely
        //    exceeds the bubble's baked-in box.
        BUBBLE_SELECTORS.forEach((sel) => {
            root.querySelectorAll(sel).forEach((el) => {
                el.style.setProperty("min-width", "0", "important");
                el.style.setProperty("max-width", "100%", "important");
                el.style.setProperty("max-height", "none", "important");
                el.style.setProperty("height", "auto", "important");
                el.style.setProperty("overflow", "visible", "important");
                el.style.setProperty("overflow-wrap", "anywhere", "important");
                el.style.setProperty("word-break", "break-word", "important");
                el.style.setProperty("white-space", "pre-wrap", "important");
                el.style.setProperty("box-sizing", "border-box", "important");
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
        stampNoOverflow(document);
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

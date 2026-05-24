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

    // Version marker — bump when shipping. The body data attribute lets you
    // confirm the latest build is actually loaded by inspecting <body> or
    // running `document.body.dataset.eraLivechatExt` in the console.
    const VERSION = "19.0.2.0.1";
    try {
        document.body && document.body.setAttribute("data-era-livechat-ext", VERSION);
        // eslint-disable-next-line no-console
        console.log("[era_live_chat_ext] loaded v" + VERSION);
    } catch (e) { /* body not ready yet — handled by the DOMContentLoaded path */ }

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

    // Icon nodes (Font Awesome, Odoo Icons, Material Icons) use a dedicated
    // font where each character code point is a glyph. Overriding their
    // font-family with the website body font renders icons as empty
    // squares — so the stamp has to skip them. The selector matches every
    // common icon container plus any class containing fa-/oi- (action
    // buttons in Odoo are usually a <button> with an icon <i> child).
    const ICON_SELECTOR = [
        "i",
        ".fa", ".fas", ".far", ".fab", ".fal",
        ".oi",
        ".material-icons", ".material-icons-outlined",
        "[class*='fa-']", "[class^='fa-']",
        "[class*='oi-']", "[class^='oi-']",
        "[class*=' fa-']", "[class*=' oi-']",
    ].join(",");

    function stampFont(root) {
        const font = bodyFont();
        if (!font) return;
        CHAT_ROOTS.forEach((sel) => {
            root.querySelectorAll(sel).forEach((container) => {
                // Stamp the container plus every descendant — input/textarea/
                // button included, since each has its own UA default. Icons
                // (matched by ICON_SELECTOR) are skipped so their glyph font
                // stays intact.
                container.style.setProperty("font-family", font, "important");
                container.querySelectorAll("*").forEach((el) => {
                    if (el.matches && el.matches(ICON_SELECTOR)) return;
                    el.style.setProperty("font-family", font, "important");
                });
            });
        });
    }

    // Real DOM inspection (Odoo 19 livechat) revealed multiple overflow
    // sources, all of which have to be neutralised together — fixing one
    // just exposes the next:
    //   - The bubble wrapper `.o-discuss-text-body` is `d-inline-block` +
    //     `overflow-x: auto` → sizes to content and paints an h-scrollbar
    //     when content exceeds its max-width.
    //   - `.o-mail-Message-richBody` *also* has `overflow-x: auto`.
    //   - Two stacked scrollers: the user-identified outer
    //     `.d-flex.flex-column.h-100.overflow-auto.o-scrollbar-thin` and
    //     `.o-mail-Thread` immediately inside it.
    //   - The flex chain from `.o-mail-Message-core` down to the bubble
    //     needs `min-width: 0` everywhere; Odoo only sets it on two nodes
    //     (`.w-100.o-min-width-0`, `.o-mail-Message-content.o-min-width-0`),
    //     and missing it on the others lets a long Arabic word push the
    //     whole row past the chat panel.
    const FLEX_CHAIN = [
        ".o-mail-Message",
        ".o-mail-Message-core",
        ".o-mail-Message-contentContainer",
        ".o-mail-Message-content",
        ".o-mail-Message-textContent",
        ".o-mail-Message-body",
        ".o-mail-Message-richBody",
        ".o-mail-Message-bubble",
        ".o-discuss-text-body",
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
        // 1. Outer + inner thread scrollers — KEEP their h-100 + overflow-y
        //    so they still fill the chat panel and scroll long history (the
        //    composer is a sibling positioned at the bottom of the flex
        //    column; stripping h-100 here makes the thread shrink to
        //    content and lifts the composer up the panel). Only kill
        //    horizontal overflow — that's the bit that matters for the
        //    Arabic-text-too-wide bug.
        root.querySelectorAll(
            ".d-flex.flex-column.h-100.overflow-auto.o-scrollbar-thin, .o-mail-Thread"
        ).forEach((el) => {
            el.style.setProperty("overflow-x", "hidden", "important");
        });

        // 2. Bubble wrapper + rich-body both ship with `overflow-x:auto` —
        //    that's the scrollbar painted inside each bubble. Kill all
        //    overflow on them; the text just wraps to a new line instead.
        root.querySelectorAll(
            ".o-discuss-text-body, .o-mail-Message-richBody, .o-mail-Message-body"
        ).forEach((el) => {
            el.style.setProperty("overflow", "visible", "important");
            el.style.setProperty("overflow-x", "visible", "important");
            el.style.setProperty("overflow-y", "visible", "important");
            el.style.setProperty("max-width", "100%", "important");
            el.style.setProperty("min-width", "0", "important");
        });

        // 3. Force `min-width:0` + `max-width:100%` down the bubble's flex
        //    chain. Flex children default to `min-width:auto` (= min-content),
        //    which on un-broken Arabic words won't shrink below the longest
        //    syllable — that's what drags the chat panel into h-scroll.
        //    Note: do NOT clear height / max-height here (Odoo flex layouts
        //    in the thread rely on intrinsic sizing of these nodes).
        FLEX_CHAIN.forEach((sel) => {
            root.querySelectorAll(sel).forEach((el) => {
                el.style.setProperty("min-width", "0", "important");
                el.style.setProperty("max-width", "100%", "important");
                el.style.setProperty("box-sizing", "border-box", "important");
            });
        });

        // 4. The actual text node — paragraphs inside the bubble. Force
        //    aggressive wrapping so even an unbreakable token splits.
        root.querySelectorAll(
            ".o-mail-Message p, .o-mail-Message-richBody p, .o-mail-Message-body p"
        ).forEach((p) => {
            p.style.setProperty("white-space", "normal", "important");
            p.style.setProperty("overflow-wrap", "anywhere", "important");
            p.style.setProperty("word-break", "break-word", "important");
            p.style.setProperty("max-width", "100%", "important");
            p.style.setProperty("min-width", "0", "important");
            p.style.setProperty("margin-bottom", "0", "important");
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

    // Wrap each pass in try/catch so a failure in one step (e.g. an Owl
    // node detached mid-walk) doesn't skip the others. Errors surface in
    // the console with the version marker so you know which build threw.
    function safe(name, fn) {
        try { fn(); }
        catch (e) {
            // eslint-disable-next-line no-console
            console.warn("[era_live_chat_ext v" + VERSION + "] " + name + " failed", e);
        }
    }

    // Collect every document we can reach: the main page plus every
    // same-origin iframe + open shadow root that exists inside it.
    // Owl mounts the embedded livechat widget into the main page in
    // Odoo 19, but some themes wrap it in an iframe for isolation,
    // and shadow-DOM mounts are possible in future Odoo versions.
    function collectRoots(root, out) {
        out.push(root);
        if (!root.querySelectorAll) return;
        root.querySelectorAll("iframe").forEach((iframe) => {
            try {
                const doc = iframe.contentDocument;
                if (doc) collectRoots(doc, out);
            } catch (e) { /* cross-origin */ }
        });
        root.querySelectorAll("*").forEach((el) => {
            if (el.shadowRoot) {
                try { collectRoots(el.shadowRoot, out); }
                catch (e) { /* closed shadow root */ }
            }
        });
    }

    function nuke() {
        const roots = [];
        collectRoots(document, roots);
        for (const doc of roots) {
            safe("scan",            () => scan(doc));
            safe("stampFont",       () => stampFont(doc));
            safe("stampNoOverflow", () => stampNoOverflow(doc));
        }
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

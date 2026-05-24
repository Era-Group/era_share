/*
 * Fatoratec livechat — text-content fallback for hiding the "Ask Human"
 * button when class-based CSS misses it (Odoo sometimes renders the button
 * as a plain <button class="btn btn-primary">اسأل بشرياً</button> with no
 * stable hook). Pairs with livechat_overrides.scss; either path is enough,
 * both kept so a template rename in a future Odoo build can't bring the
 * button back without us noticing.
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

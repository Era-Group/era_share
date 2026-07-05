/*
 * Fatoratec livechat — make the Enter key send the message on mobile too.
 *
 * Odoo's mail Composer.onKeydown (mail/static/src/core/common/composer.js)
 * early-returns when `this.isMobileOS` is true:
 *
 *     if (this.isMobileOS || ev.isComposing) { return; }
 *     const shouldPost = this.env.inChatter ? ev.ctrlKey : !ev.shiftKey;
 *
 * So on desktop plain Enter (without Shift) already sends, but on phones and
 * tablets Enter just inserts a newline and the visitor has to tap the send
 * button. We want Enter to send on every device in the chat.
 *
 * The fix lifts ONLY the mobile gate, and ONLY for livechat threads, by
 * clearing `isMobileOS` for the duration of the keydown and then running
 * Odoo's stock handler — so Enter is treated exactly as it is on desktop.
 * Everything else is preserved by delegating to super:
 *   - Shift+Enter still inserts a newline (`!ev.shiftKey` in the base handler);
 *   - the IME composition guard (`ev.isComposing`) still blocks send mid-input;
 *   - non-Enter keys, and every non-livechat composer (backend Discuss, portal
 *     chatter), keep Odoo's original behaviour untouched.
 *
 * `isMobileOS` is a plain (non-reactive) instance property set once in
 * Composer.setup(). Inside composer.js it is read only in onKeydown; the
 * composer template also reads it (an `o-mobile` class and the "press Enter to
 * send" hint), but because it is not reactive and we restore it synchronously
 * within the same tick — before any re-render can run — flipping it here has no
 * visual effect and changes nothing but this one Enter-to-send decision.
 *
 * Known limitation (inherent to mobile web, not this patch): some Android/iOS
 * soft keyboards fire the return key as keyCode 229 / `key === "Unidentified"`
 * (with `isComposing` true) under predictive/IME input instead of a real
 * `key === "Enter"` keydown. On those, `ev.key !== "Enter"` short-circuits to
 * Odoo's stock behaviour and the visitor uses the Send button — no crash, no
 * regression, just no Enter-to-send on that particular keyboard state. This is
 * exactly why Odoo gates Enter-to-send behind `!isMobileOS` in the first place;
 * it cannot be fixed from within a keydown handler.
 */
import { Composer } from "@mail/core/common/composer";
import { patch } from "@web/core/utils/patch";

patch(Composer.prototype, {
    onKeydown(ev) {
        if (
            ev.key !== "Enter" ||
            !this.isMobileOS ||
            this.thread?.channel_type !== "livechat"
        ) {
            return super.onKeydown(ev);
        }
        const wasMobileOS = this.isMobileOS;
        this.isMobileOS = false;
        try {
            return super.onKeydown(ev);
        } finally {
            this.isMobileOS = wasMobileOS;
        }
    },
});

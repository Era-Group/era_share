/*
 * Fatoratec livechat — auto-open the conversation on mobile too.
 *
 * Odoo's AutopopupService.allowAutoPopup gate includes `!this.ui.isSmall`,
 * so the chat auto-pops on desktop but never on phones — on the standalone
 * support page (/im_livechat/support/<id>) that leaves mobile visitors
 * staring at just the launcher button while desktop opens the conversation
 * straight away.
 *
 * We relax the gate ONLY on the support page (whose entire purpose is the
 * chat), so regular website/content pages keep Odoo's intentional
 * non-intrusive mobile behaviour (no full-screen chat popping over content).
 */
import { patch } from "@web/core/utils/patch";
import { AutopopupService } from "@im_livechat/embed/common/autopopup_service";
import { expirableStorage } from "@im_livechat/core/common/expirable_storage";

// True on the dedicated external support page: /im_livechat/support/<channel_id>.
function onSupportPage() {
    try {
        return window.location.pathname.startsWith("/im_livechat/support/");
    } catch (e) {
        return false;
    }
}

patch(AutopopupService.prototype, {
    get allowAutoPopup() {
        // Mirror of Odoo's original getter, but the small-screen block is
        // lifted on the support page so mobile opens the conversation like
        // desktop. Everything else (already-popped flag, rule action,
        // availability, no active session) is preserved unchanged.
        return Boolean(
            !expirableStorage.getItem(AutopopupService.STORAGE_KEY) &&
                (onSupportPage() || !this.ui.isSmall) &&
                this.storeService.livechat_rule?.action === "auto_popup" &&
                this.storeService.livechat_available &&
                this.storeService.activeVisitorLivechats.length === 0
        );
    },
});

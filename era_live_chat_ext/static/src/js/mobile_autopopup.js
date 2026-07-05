/*
 * Fatoratec livechat — honor the channel's "Open automatically" rule on every
 * device, so the conversation auto-opens on mobile too.
 *
 * Odoo's AutopopupService.allowAutoPopup gate includes `!this.ui.isSmall`,
 * so even when a livechat channel *rule* is set to "Open automatically"
 * (action === "auto_popup") the chat only auto-pops on desktop and never on
 * phones. On the website support page (/support) and the standalone external
 * page (/im_livechat/support/<id>) — both explicitly configured with the
 * auto_popup action — that leaves mobile visitors staring at just the launcher
 * button while desktop opens the conversation straight away.
 *
 * The fix keys the behaviour off the channel setting instead of a hardcoded
 * URL: we simply drop the small-screen block. Every other condition is kept,
 * including `livechat_rule?.action === "auto_popup"` — which is the server's
 * answer to "does the rule matched for THIS page URL say open automatically?"
 * (im_livechat matches the current page against im_livechat.channel.rule.regex_url
 * in controllers/webclient.py `init_livechat`). So:
 *   - pages the admin set to "Open automatically" (e.g. /support) now auto-open
 *     on mobile *and* desktop — as per the settings in the livechat channel;
 *   - pages left on "Show" / "Show with notification" (e.g. the home page,
 *     action === "display_button_and_text") still never auto-pop, on any device,
 *     so regular content pages keep Odoo's intentional non-intrusive behaviour.
 *
 * On desktop this is a no-op: `!this.ui.isSmall` was already true there, so the
 * expression is unchanged. The only behavioural difference is on small screens,
 * and only for pages whose channel rule is auto_popup.
 */
import { patch } from "@web/core/utils/patch";
import { AutopopupService } from "@im_livechat/embed/common/autopopup_service";
import { expirableStorage } from "@im_livechat/core/common/expirable_storage";

patch(AutopopupService.prototype, {
    get allowAutoPopup() {
        // Mirror of Odoo's original getter with the small-screen block removed.
        // Everything else (already-popped flag, the channel rule's auto_popup
        // action, availability, and "no active session yet") is preserved, so
        // the auto-open decision follows the livechat channel rule matched for
        // the current page — on mobile exactly as on desktop.
        return Boolean(
            !expirableStorage.getItem(AutopopupService.STORAGE_KEY) &&
                this.storeService.livechat_rule?.action === "auto_popup" &&
                this.storeService.livechat_available &&
                this.storeService.activeVisitorLivechats.length === 0
        );
    },
});

import { Composer } from "@mail/core/common/composer";
import { _t } from "@web/core/l10n/translation";
import { patch } from "@web/core/utils/patch";

patch(Composer.prototype, {
    get placeholder() {
        // The enterprise whatsapp composer reads `wa_account_id.name` without checking
        // that the account is there. A channel whose account was deleted or archived —
        // the FK nulls the column rather than removing the channel — then takes the whole
        // Discuss view down with "Cannot read properties of undefined". Say so in the
        // composer instead, so the conversation stays readable.
        if (this.thread?.channel_type === "whatsapp" && !this.thread.wa_account_id) {
            return _t("This conversation's WhatsApp account is no longer available.");
        }
        return super.placeholder;
    },
});

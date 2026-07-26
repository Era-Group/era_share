import { Message } from "@mail/core/common/message_model";
import { patch } from "@web/core/utils/patch";

patch(Message.prototype, {
    get authorName() {
        const name = super.authorName;
        const thread = this.thread;
        const phone = this.author?.phone;
        // Odoo renders internal/user-linked partners as clickable contacts. Keep their
        // concise name; expose a number only for external, non-clickable senders.
        if (thread?.is_waha_group_channel && phone && !this.author?.main_user_id && phone !== name) {
            return `${name} (${phone})`;
        }
        return name;
    },
});

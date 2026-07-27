import { Message } from "@mail/core/common/message_model";
import { fields } from "@mail/core/common/record";
import { DiscussCoreCommon } from "@mail/discuss/core/common/discuss_core_common_service";
import { patch } from "@web/core/utils/patch";

// Channel bus updates are broadcast to every member for message synchronization.
// WAHA has a separate, narrower notification audience; do not turn a broadcast
// received by a non-recipient into an unread counter or a client-side alert.
patch(Message.prototype, {
    setup() {
        super.setup();
        this.waha_notification_policy_applied = fields.Attr(false);
        this.waha_notification_partner_ids = fields.Many("res.partner");
    },
});

patch(DiscussCoreCommon.prototype, {
    async _handleNotificationNewMessage(payload, metadata) {
        const message = this.store["mail.message"].get(payload.data["mail.message"][0]);
        const isWahaNonRecipient =
            message?.thread?.is_waha_channel &&
            message.waha_notification_policy_applied &&
            !message.isSelfAuthored &&
            !message.waha_notification_partner_ids.some(
                (partner) => partner.id === this.store.self_partner?.id
            );
        await super._handleNotificationNewMessage(payload, metadata);
        if (!message || !isWahaNonRecipient || message.isNotification) {
            return;
        }
        const member = message.thread?.self_member_id;
        if (!member) {
            return;
        }
        member.message_unread_counter = Math.max(0, member.message_unread_counter - 1);
        if (member.new_message_separator_ui === message.id) {
            member.new_message_separator_ui = 0;
        }
    },
});

import { fields } from "@mail/core/common/record";
import { Message } from "@mail/core/common/message_model";
import { patch } from "@web/core/utils/patch";

// Stores the WAHA account-protection block reason on a failed outbound message so the
// Discuss failed-post tooltip can explain *why* it wasn't sent, instead of the generic
// "Failed to post the message. Click to retry". Set in Store.doMessagePost; stays empty
// for every non-WAHA message, so their tooltip is unchanged.
patch(Message.prototype, {
    setup() {
        super.setup(...arguments);
        this.wahaBlockReason = fields.Attr("");
    },
});

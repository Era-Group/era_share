import { Thread } from "@mail/core/common/thread_model";
import { fields } from "@mail/core/common/record";

import { patch } from "@web/core/utils/patch";

patch(Thread.prototype, {
    setup() {
        super.setup(...arguments);
        /* The Cc recipients are the recipients manually added by the user in
         * the "Cc" field of the Chatter. They are notified like the "To"
         * recipients, but are recorded on the message as being in copy. */
        this.ccRecipients = fields.Attr([]);
    },
});

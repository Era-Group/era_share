import { fields } from "@mail/core/common/record";
import { Thread } from "@mail/core/common/thread_model";
import { patch } from "@web/core/utils/patch";

patch(Thread.prototype, {
    setup() {
        super.setup(...arguments);
        this.is_waha_channel = fields.Attr(false);
    },
    _computeDiscussAppCategory() {
        // Route WAHA channels to their own sidebar category; everything else
        // (including Meta WhatsApp channels) keeps the standard behavior.
        if (this.is_waha_channel) {
            return this.store.discuss.waha;
        }
        return super._computeDiscussAppCategory();
    },
});

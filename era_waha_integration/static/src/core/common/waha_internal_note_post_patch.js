import { Store } from "@mail/core/common/store_service";
import { patch } from "@web/core/utils/patch";

patch(Store.prototype, {
    async getMessagePostParams({ body, postData, thread }) {
        const params = await super.getMessagePostParams({ body, postData, thread });
        if (postData.wahaInternalNote && thread.channel_type === "whatsapp") {
            params.post_data.waha_internal_note = true;
            params.post_data.message_type = "comment";
            params.post_data.subtype_xmlid = "mail.mt_note";
        }
        return params;
    },
});

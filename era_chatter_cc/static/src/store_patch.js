import { Store } from "@mail/core/common/store_service";

import { patch } from "@web/core/utils/patch";

patch(Store.prototype, {
    async getMessagePostParams({ body, postData, thread }) {
        const params = await super.getMessagePostParams(...arguments);
        if (postData.isNote || !thread.ccRecipients?.length) {
            return params;
        }
        const ccIds = [];
        const ccEmails = [];
        for (const recipient of thread.ccRecipients) {
            if (recipient.partner_id) {
                ccIds.push(recipient.partner_id);
            } else {
                ccEmails.push(recipient.email);
            }
        }
        if (ccIds.length) {
            params.post_data.partner_cc_ids = ccIds;
        }
        if (ccEmails.length) {
            params.post_data.partner_cc_emails = ccEmails;
        }
        return params;
    },
});

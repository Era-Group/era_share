import { Store } from "@mail/core/common/store_service";
import { patch } from "@web/core/utils/patch";
import { rpc } from "@web/core/network/rpc";
import { _t } from "@web/core/l10n/translation";

// A WAHA WhatsApp send that the account-protection guard blocks raises a clean
// UserError carrying the reason (e.g. "this contact hasn't replied yet…"). But Discuss
// posts messages with rpc {silent:true} and swallows that error, leaving only a generic
// "Failed to post the message. Click to retry" tooltip — the reason is lost. This
// override surfaces the reason for WAHA channels: a sticky toast plus the text stored on
// the failed message (for its tooltip). It mirrors the core doMessagePost exactly
// otherwise, so non-WAHA channels behave identically.
patch(Store.prototype, {
    async doMessagePost(params, tmpMessage) {
        return this.messagePostMutex.exec(async () => {
            let res;
            try {
                res = await rpc("/mail/message/post", params, { silent: true });
            } catch (err) {
                if (!tmpMessage) {
                    throw err;
                }
                if (
                    tmpMessage.thread?.is_waha_channel &&
                    err?.exceptionName === "odoo.exceptions.UserError"
                ) {
                    const reason = err.data?.message || err.message;
                    if (reason) {
                        tmpMessage.wahaBlockReason = reason;
                        this.env.services.notification.add(reason, {
                            title: _t("WhatsApp message not sent"),
                            type: "warning",
                            sticky: true,
                        });
                    }
                }
                tmpMessage.postFailRedo = () => {
                    tmpMessage.postFailRedo = undefined;
                    tmpMessage.thread.messages.delete(tmpMessage);
                    tmpMessage.thread.messages.add(tmpMessage);
                    this.doMessagePost(params, tmpMessage);
                };
            }
            return res;
        });
    },
});

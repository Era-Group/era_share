/** @odoo-module **/

import { Chatter } from "@mail/chatter/web_portal/chatter";
import { patch } from "@web/core/utils/patch";
import { useState, useEffect } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";

/**
 * Patches the Chatter to add a WhatsApp shortcut button that opens the floating
 * window on the current record's contact conversation. Visible only when the
 * record's partner has a whatsapp channel and the user may see the float.
 */
patch(Chatter.prototype, {
    setup() {
        super.setup(...arguments);
        this.floatService = useService("era_waha_float");
        this.waState = useState({ channelId: null });
        this._waFetchSeq = 0;

        useEffect(
            () => {
                this._fetchWaChannel(this.props.threadModel, this.props.threadId);
            },
            () => [this.props.threadModel, this.props.threadId]
        );
    },

    async _fetchWaChannel(threadModel, threadId) {
        if (!threadId || !threadModel) {
            this.waState.channelId = null;
            return;
        }
        const seq = ++this._waFetchSeq;
        const channelId = await this.orm.call(
            "discuss.channel",
            "get_whatsapp_channel_for_record",
            [threadModel, threadId]
        );
        if (seq !== this._waFetchSeq) {
            return;
        }
        this.waState.channelId = channelId || null;
    },

    get waButtonVisible() {
        return !!(this.waState?.channelId && this.floatService?.state.allowed);
    },

    openWhatsappFromChatter() {
        if (!this.waState.channelId) {
            return;
        }
        this.floatService.openChannel(this.waState.channelId);
    },
});

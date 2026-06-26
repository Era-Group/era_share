/** @odoo-module **/

import { Chatter } from "@mail/chatter/web_portal/chatter";
import { patch } from "@web/core/utils/patch";
import { useState, useEffect } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";

/**
 * Patches the Chatter component to add a WhatsApp shortcut button.
 *
 * The button appears only when:
 *  - The current record's partner has an existing WhatsApp discuss.channel
 *  - The current user has access to the WhatsApp float service
 *
 * Clicking it opens the floating WhatsApp Discuss window and navigates
 * directly to that contact's conversation.
 */
patch(Chatter.prototype, {
    setup() {
        super.setup(...arguments);
        this.floatService = useService("era_whatsapp_float");
        this.waState = useState({ channelId: null });
        this._waFetchSeq = 0;

        // Re-check whenever the viewed record changes.
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
        // Guard against stale responses when the user navigates quickly.
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

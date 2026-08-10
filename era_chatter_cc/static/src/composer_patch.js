import { Composer } from "@mail/core/common/composer";

import { rpc } from "@web/core/network/rpc";
import { isEmail } from "@web/core/utils/strings";
import { patch } from "@web/core/utils/patch";
import { toRaw } from "@odoo/owl";

patch(Composer.prototype, {
    async sendMessage() {
        const composer = toRaw(this.props.composer);
        if (this.props.type !== "note" && !composer.message) {
            // same silent guard core uses for the "To" line: keep the invalid
            // tag highlighted instead of posting a broken message.
            const ccRecipients = composer.thread?.ccRecipients ?? [];
            if (ccRecipients.some((recipient) => !recipient.email || !isEmail(recipient.email))) {
                return;
            }
        }
        return super.sendMessage(...arguments);
    },

    async _sendMessage() {
        const message = await super._sendMessage(...arguments);
        this.props.composer.thread.ccRecipients = [];
        return message;
    },

    async onClickFullComposer(ev) {
        if (this.props.type !== "note") {
            await this._ensureCcPartners();
        }
        return super.onClickFullComposer(...arguments);
    },

    get fullComposerAdditionalContext() {
        const context = super.fullComposerAdditionalContext;
        if (this.props.type === "note") {
            return context;
        }
        const ccIds = (this.thread.ccRecipients ?? [])
            .filter((recipient) => recipient.partner_id)
            .map((recipient) => recipient.partner_id);
        return ccIds.length ? { ...context, default_partner_cc_ids: ccIds } : context;
    },

    /**
     * Turn the Cc entries that are not linked to a partner yet into real
     * partners, exactly as core does for the "To" line, so the full composer
     * can be pre-filled with them.
     */
    async _ensureCcPartners() {
        const ccRecipients = this.thread.ccRecipients ?? [];
        const newPartners = ccRecipients.filter((recipient) => !recipient.partner_id);
        if (!newPartners.length) {
            return;
        }
        const recipientEmails = newPartners.map((recipient) => recipient.email);
        const partners = await rpc("/mail/partner/from_email", {
            thread_model: this.thread.model,
            thread_id: this.thread.id,
            emails: recipientEmails,
        });
        for (const index in partners) {
            const partner = this.store["res.partner"].insert(partners[index]);
            const email = recipientEmails[index];
            const recipient = ccRecipients.find((recipient) => recipient.email === email);
            if (recipient) {
                recipient.partner_id = partner.id;
            }
        }
    },
});

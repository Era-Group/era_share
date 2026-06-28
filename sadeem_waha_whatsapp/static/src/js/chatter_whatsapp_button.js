/** @odoo-module **/

import { Chatter } from "@mail/chatter/web_portal/chatter";
import { _t } from "@web/core/l10n/translation";
import { patch } from "@web/core/utils/patch";

patch(Chatter.prototype, {
    /**
     * Send WhatsApp message via WAHA from chatter
     */
    sendWahaWhatsapp() {
        const send = async (thread) => {
            let phoneNumber = null;
            let partnerId = false;

            try {
                // Try different field paths to find phone number
                const phoneFieldPaths = [
                    'waha_whatsapp_number',
                    'phone',
                    'partner_id.waha_whatsapp_number',
                    'partner_id.phone',
                ];

                // For res.partner, set partnerId directly
                if (thread.model === 'res.partner') {
                    partnerId = thread.id;
                }

                // Try each field path until we find a phone number
                for (const fieldPath of phoneFieldPaths) {
                    if (phoneNumber) break;

                    try {
                        const fields = fieldPath.split('.');

                        if (fields.length === 1) {
                            // Direct field (e.g., 'mobile', 'phone')
                            const data = await this.env.services.orm.read(
                                thread.model,
                                [thread.id],
                                [fieldPath]
                            );

                            if (data && data.length > 0 && data[0][fieldPath]) {
                                phoneNumber = data[0][fieldPath];
                            }
                        } else if (fields.length === 2 && fields[0] === 'partner_id') {
                            // Relational field (e.g., 'partner_id.mobile')
                            const data = await this.env.services.orm.read(
                                thread.model,
                                [thread.id],
                                ['partner_id']
                            );

                            if (data && data.length > 0 && data[0].partner_id) {
                                const partnerIdValue = Array.isArray(data[0].partner_id)
                                    ? data[0].partner_id[0]
                                    : data[0].partner_id;

                                const partnerData = await this.env.services.orm.read(
                                    'res.partner',
                                    [partnerIdValue],
                                    [fields[1]]
                                );

                                if (partnerData && partnerData.length > 0 && partnerData[0][fields[1]]) {
                                    phoneNumber = partnerData[0][fields[1]];
                                    partnerId = partnerIdValue;
                                }
                            }
                        }
                    } catch (fieldError) {
                        // Field doesn't exist on this model, try next field path
                        continue;
                    }
                }

                if (!phoneNumber) {
                    this.env.services.notification.add(
                        _t("No WhatsApp number found for this record, please add mobile or phone number"),
                        { type: 'warning' }
                    );
                    return;
                }
            } catch (error) {
                console.error('Error getting phone number:', error);
                this.env.services.notification.add(
                    _t("Could not retrieve phone number for this record"),
                    { type: 'danger' }
                );
                return;
            }

            // Open send WhatsApp wizard using the named action to avoid
            // conflict with the built-in Odoo WhatsApp module action.
            await new Promise((resolve) => {
                this.env.services.action.doAction(
                    'sadeem_waha_whatsapp.action_send_whatsapp_wizard',
                    {
                        additionalContext: {
                            active_model: thread.model,
                            active_id: thread.id,
                            default_phone_number: phoneNumber,
                            default_partner_id: partnerId,
                        },
                        onClose: resolve,
                    }
                );
            });

            // Refresh messages after wizard closes
            this.store.Thread.insert({
                model: this.props.threadModel,
                id: this.props.threadId,
            }).fetchNewMessages();
        };

        if (this.state.thread.id) {
            send(this.state.thread);
        } else {
            this.onThreadCreated = send;
            this.props.saveRecord?.();
        }
    },
});

/** @odoo-module **/

import { registry } from "@web/core/registry";
import { Component } from "@odoo/owl";
import { WhatsappChatDialog } from "./whatsapp_chat_dialog";

export class WizardViewChatButton extends Component {
    static template = "sadeem_waha_whatsapp.WizardViewChatButton";
    static props = ["record", "readonly", "class?"];

    openChat() {
        const data = this.props.record.data;

        // In Odoo 18/19, Many2one fields in record.data are objects {id, display_name}
        const sessionId = data.session_id && typeof data.session_id === 'object'
            ? data.session_id.id
            : data.session_id;
        const partnerId = data.partner_id && typeof data.partner_id === 'object'
            ? data.partner_id.id
            : (data.partner_id || false);
        const partnerName = data.partner_id && typeof data.partner_id === 'object'
            ? data.partner_id.display_name
            : data.phone_number;

        if (!sessionId) {
            this.env.services.notification.add("Please select a WhatsApp session first", { type: "warning" });
            return;
        }
        if (!data.phone_number) {
            this.env.services.notification.add("Please enter a phone number first", { type: "warning" });
            return;
        }

        let phone = (data.phone_number || "").trim().replace(/^\+/, "").replace(/\D/g, "");
        const chatId = `${phone}@c.us`;

        const remove = this.env.services.overlay.add(WhatsappChatDialog, {
            chatId: chatId,
            sessionId: sessionId,
            phoneNumber: data.phone_number,
            partnerId: partnerId || false,
            partnerName: partnerName || data.phone_number,
            close: () => remove(),
        });
    }
}

registry.category("view_widgets").add("wizard_view_chat_button", {
    component: WizardViewChatButton,
});

/** @odoo-module **/

import { registry } from "@web/core/registry";
import { Component } from "@odoo/owl";
import { WhatsappChatDialog } from "./whatsapp_chat_dialog";

export class WhatsappChatButton extends Component {
    static template = "sadeem_waha_whatsapp.WhatsappChatButton";
    static props = ["record", "readonly", "class"];

    setup() {}

    openChat() {
        const record = this.props.record.data;
        // In Odoo 18/19, Many2one fields in record.data are objects {id, display_name}
        const sessionId = record.session_id && typeof record.session_id === 'object'
            ? record.session_id.id
            : record.session_id;
        const partnerId = record.partner_id && typeof record.partner_id === 'object'
            ? record.partner_id.id
            : (record.partner_id || false);
        const partnerName = record.partner_id && typeof record.partner_id === 'object'
            ? record.partner_id.display_name
            : record.phone_number;

        const remove = this.env.services.overlay.add(WhatsappChatDialog, {
            chatId: record.chat_id,
            sessionId: sessionId,
            phoneNumber: record.phone_number,
            partnerId: partnerId || false,
            partnerName: partnerName || record.phone_number,
            close: () => remove(),
        });
    }
}

registry.category("view_widgets").add("whatsapp_chat_button", {
    component: WhatsappChatButton,
});

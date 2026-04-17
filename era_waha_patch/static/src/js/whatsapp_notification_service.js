/** @odoo-module **/

import { registry } from "@web/core/registry";
import { Component, xml } from "@odoo/owl";
import { WhatsappChatDialog } from "@sadeem_waha_whatsapp/js/whatsapp_chat_dialog";
import { useService } from "@web/core/utils/hooks";

class OpenWhatsappChat extends Component {
    static template = xml`<div/>`;
    static props = ["*"];

    setup() {
        const dialog = useService("dialog");
        const p = this.props.action.params || {};
        dialog.add(WhatsappChatDialog, {
            chatId: p.chat_id,
            sessionId: p.session_id,
            phoneNumber: p.phone_number,
            partnerId: p.partner_id || false,
            partnerName: p.partner_name || p.phone_number,
        });
    }
}

registry.category("actions").add("era_waha_open_chat", OpenWhatsappChat);

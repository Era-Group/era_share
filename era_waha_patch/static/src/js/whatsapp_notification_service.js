/** @odoo-module **/

import { registry } from "@web/core/registry";
import { Component, xml } from "@odoo/owl";
import { WhatsappChatDialog } from "@sadeem_waha_whatsapp/js/whatsapp_chat_dialog";
import { useService } from "@web/core/utils/hooks";
import { patch } from "@web/core/utils/patch";
import { Thread } from "@mail/core/common/thread_model";

patch(Thread.prototype, {
    open(options) {
        if (this.model === "sadeem.waha.whatsapp.message") {
            const store = this.store;
            store.env.services.orm
                .read("sadeem.waha.whatsapp.message", [this.id], [
                    "phone_number",
                    "session_id",
                    "chat_id",
                    "partner_id",
                ])
                .then((records) => {
                    if (!records.length) return;
                    const rec = records[0];
                    const phone = (rec.phone_number || "")
                        .replace(/^\+/, "")
                        .replace(/\D/g, "");
                    store.env.services.dialog.add(WhatsappChatDialog, {
                        chatId: rec.chat_id || `${phone}@c.us`,
                        sessionId: rec.session_id[0],
                        phoneNumber: rec.phone_number || "",
                        partnerId: rec.partner_id ? rec.partner_id[0] : false,
                        partnerName: rec.partner_id
                            ? rec.partner_id[1]
                            : rec.phone_number || "",
                    });
                });
            return true;
        }
        return super.open(...arguments);
    },
});

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

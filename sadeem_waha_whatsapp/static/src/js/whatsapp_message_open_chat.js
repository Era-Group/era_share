/** @odoo-module **/

/** @odoo-module **/

import { registry } from "@web/core/registry";
import { Component, xml } from "@odoo/owl";
import { WhatsappChatDialog } from "./whatsapp_chat_dialog";
import { useService } from "@web/core/utils/hooks";
import { patch } from "@web/core/utils/patch";
import { Thread } from "@mail/core/common/thread_model";

patch(Thread.prototype, {
    open(options) {
        if (this.model === "sadeem.waha.whatsapp.message") {
            this.markAllMessagesAsRead();
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
                    const remove = store.env.services.overlay.add(WhatsappChatDialog, {
                        chatId: rec.chat_id || `${phone}@c.us`,
                        sessionId: rec.session_id[0],
                        phoneNumber: rec.phone_number || "",
                        partnerId: rec.partner_id ? rec.partner_id[0] : false,
                        partnerName: rec.partner_id ? rec.partner_id[1] : rec.phone_number || "",
                        close: () => remove(),
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
        const overlay = useService("overlay");
        const p = this.props.action.params || {};
        const remove = overlay.add(WhatsappChatDialog, {
            chatId: p.chat_id,
            sessionId: p.session_id,
            phoneNumber: p.phone_number,
            partnerId: p.partner_id || false,
            partnerName: p.partner_name || p.phone_number,
            close: () => remove(),
        });
    }
}

registry.category("actions").add("sadeem_waha_open_chat", OpenWhatsappChat);

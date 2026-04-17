/** @odoo-module **/

import { registry } from "@web/core/registry";
import { WhatsappChatDialog } from "@sadeem_waha_whatsapp/js/whatsapp_chat_dialog";

const whatsappNotificationService = {
    dependencies: ["bus_service", "notification", "dialog"],

    start(env, { bus_service, notification, dialog }) {
        bus_service.subscribe("era_waha_patch/whatsapp_incoming", (payload) => {
            const phone = payload.phone_number || "";
            const name = payload.partner_name || phone;
            const preview = payload.preview || "";
            const title = `💬 WhatsApp - ${name}`;
            const message = preview || `New message from ${phone}`;

            notification.add(message, {
                title,
                type: "info",
                sticky: true,
                buttons: [
                    {
                        name: "Open Chat",
                        primary: true,
                        onClick: () => {
                            let chatId = phone.replace(/^\+/, "").replace(/\D/g, "");
                            chatId = `${chatId}@c.us`;
                            dialog.add(WhatsappChatDialog, {
                                chatId,
                                sessionId: payload.session_id,
                                phoneNumber: phone,
                                partnerId: payload.partner_id || false,
                                partnerName: name,
                            });
                        },
                    },
                ],
            });
        });
    },
};

registry.category("services").add("whatsapp_incoming_notify", whatsappNotificationService);

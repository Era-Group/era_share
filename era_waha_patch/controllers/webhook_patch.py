import logging
from datetime import timedelta
from odoo.fields import Datetime
from odoo.http import request
from odoo.addons.sadeem_waha_whatsapp.controllers.webhook_controller import (
    WhatsAppWebhookController,
)

_logger = logging.getLogger(__name__)

NOTIFY_COOLDOWN_MINUTES = 2


class WhatsAppWebhookControllerPatch(WhatsAppWebhookController):

    def _notify_new_message(self, message):
        try:
            session = message.session_id
            partner_ids = session.notify_user_ids.mapped("partner_id").ids
            if not partner_ids:
                return

            cutoff = Datetime.to_string(
                Datetime.now() - timedelta(minutes=NOTIFY_COOLDOWN_MINUTES)
            )
            recent = request.env["mail.message"].sudo().search_count([
                ("model", "=", "sadeem.waha.whatsapp.message"),
                ("body", "ilike", message.phone_number),
                ("message_type", "=", "notification"),
                ("date", ">=", cutoff),
            ], limit=1)
            if recent:
                return

            bus = request.env["bus.bus"].sudo()
            for pid in partner_ids:
                partner = request.env["res.partner"].sudo().browse(pid)
                bus._sendone(partner, "era_waha_patch/whatsapp_incoming", {
                    "phone_number": message.phone_number,
                    "session_id": session.id,
                    "chat_id": message.chat_id,
                    "partner_id": message.partner_id.id or False,
                    "partner_name": (
                        message.partner_id.name
                        if message.partner_id
                        else message.phone_number
                    ),
                    "preview": (message.text or "")[:100],
                })
        except Exception as e:
            _logger.error("Error sending notification: %s", e)

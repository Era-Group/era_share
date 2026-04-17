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

            message.with_user(request.env.ref("base.user_root")).message_post(
                body="New WhatsApp message received from %s" % message.phone_number,
                message_type="notification",
                partner_ids=partner_ids,
            )
        except Exception as e:
            _logger.error("Error sending notification: %s", e)

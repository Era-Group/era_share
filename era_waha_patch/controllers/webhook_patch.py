import logging
from odoo.http import request
from odoo.addons.sadeem_waha_whatsapp.controllers.webhook_controller import (
    WhatsAppWebhookController,
)

_logger = logging.getLogger(__name__)


def _notify_new_message_patched(self, message):
    try:
        session = message.session_id
        partner_ids = session.notify_user_ids.mapped("partner_id").ids
        if not partner_ids:
            return

        unread = request.env["mail.notification"].sudo().search_count([
            ("res_partner_id", "in", partner_ids),
            ("mail_message_id.model", "=", "sadeem.waha.whatsapp.message"),
            ("mail_message_id.body", "ilike", message.phone_number),
            ("is_read", "=", False),
        ], limit=1)
        if unread:
            return

        message.with_user(request.env.ref("base.user_root")).message_post(
            body="New WhatsApp message received from %s" % message.phone_number,
            message_type="notification",
            partner_ids=partner_ids,
        )
    except Exception as e:
        _logger.error("Error sending notification: %s", e)


WhatsAppWebhookController._notify_new_message = _notify_new_message_patched

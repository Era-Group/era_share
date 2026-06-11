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

        Notif = request.env["mail.notification"].sudo()
        partners_to_notify = []
        for pid in partner_ids:
            unread = Notif.search_count([
                ("res_partner_id", "=", pid),
                ("mail_message_id.model", "=", "sadeem.waha.whatsapp.message"),
                ("mail_message_id.body", "ilike", message.phone_number),
                ("is_read", "=", False),
            ], limit=1)
            if not unread:
                partners_to_notify.append(pid)

        if not partners_to_notify:
            return

        message.with_user(request.env.ref("base.user_root")).message_post(
            body="New WhatsApp message received from %s" % message.phone_number,
            message_type="notification",
            partner_ids=partners_to_notify,
        )
    except Exception as e:
        _logger.error("Error sending notification: %s", e)


WhatsAppWebhookController._notify_new_message = _notify_new_message_patched

import logging
from odoo.http import request
from odoo.addons.sadeem_waha_whatsapp.controllers.webhook_controller import (
    WhatsAppWebhookController,
)

_logger = logging.getLogger(__name__)


class WhatsAppWebhookControllerPatch(WhatsAppWebhookController):

    def _notify_new_message(self, message):
        try:
            session = message.session_id
            partner_ids = session.notify_user_ids.mapped("partner_id").ids
            _logger.warning(
                "ERA_PATCH notify: msg=%s phone=%s partners=%s",
                message.id, message.phone_number, partner_ids,
            )
            if not partner_ids:
                return

            unread = request.env["mail.notification"].sudo().search_count([
                ("res_partner_id", "in", partner_ids),
                ("mail_message_id.model", "=", "sadeem.waha.whatsapp.message"),
                ("mail_message_id.body", "ilike", message.phone_number),
                ("is_read", "=", False),
            ], limit=1)
            _logger.warning("ERA_PATCH unread=%s phone=%s", unread, message.phone_number)
            if unread:
                return

            message.with_user(request.env.ref("base.user_root")).message_post(
                body="New WhatsApp message received from %s" % message.phone_number,
                message_type="notification",
                partner_ids=partner_ids,
            )
            _logger.warning("ERA_PATCH notification sent for %s", message.phone_number)
        except Exception as e:
            _logger.error("ERA_PATCH error: %s", e)

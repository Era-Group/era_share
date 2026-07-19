# Part of Era Group custom addons.
import logging

from odoo import models
from odoo.addons.mail.tools.discuss import Store

_logger = logging.getLogger(__name__)


class MailMessage(models.Model):
    _inherit = 'mail.message'

    def _message_reaction(self, content, action, partner, guest, store: Store = None):
        """The standard WhatsApp override pushes reactions to the Meta Graph API for any
        'whatsapp_message'. For WAHA-backed messages that would crash, so we handle the
        reaction locally + via WAHA and skip the Meta path entirely."""
        wa_message = self.wa_message_ids[:1]
        if (self.message_type == 'whatsapp_message' and wa_message
                and wa_message.wa_account_id.provider == 'waha'):
            existing = self.env['mail.message.reaction'].search([
                ('message_id', '=', self.id),
                ('partner_id', '=', partner.id),
                ('guest_id', '=', guest.id),
            ], limit=1)
            if action == 'add':
                if existing:
                    if existing.content == content:
                        return
                    previous = existing.content
                    existing.unlink()
                    self._bus_send_reaction_group(previous)
                self.env['mail.message.reaction'].create({
                    'message_id': self.id,
                    'content': content,
                    'partner_id': partner.id,
                    'guest_id': guest.id,
                })
            elif existing:
                existing.unlink()
            self._bus_send_reaction_group(content)
            try:
                wa_message.wa_account_id._waha_react(
                    wa_message, content if action == 'add' else '')
            except Exception:
                _logger.exception("WAHA: failed to send reaction for message %s", self.id)
            return
        return super()._message_reaction(content, action, partner, guest, store)

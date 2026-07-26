# Part of Era Group custom addons.
import logging

from odoo import models
from odoo.addons.mail.tools.discuss import Store

_logger = logging.getLogger(__name__)


class MailMessage(models.Model):
    _inherit = 'mail.message'

    def _waha_group_authors_to_store(self, store):
        """Expose sender phone numbers only for WAHA group messages.

        The standard Discuss store deliberately serializes authors with no contact
        fields, so the browser cannot otherwise render the number beside the name.
        """
        group_messages = self.filtered(
            lambda m: m.model == 'discuss.channel'
            and m.message_type == 'whatsapp_message'
            and self.env['discuss.channel'].browse(m.res_id).is_waha_group_channel)
        if group_messages:
            store.add(group_messages, [
                Store.One('author_id', ['phone']),
            ])

    def _extras_to_store(self, store, format_reply):
        super()._extras_to_store(store, format_reply=format_reply)
        self._waha_group_authors_to_store(store)

    def _message_notifications_to_store(self, store):
        super()._message_notifications_to_store(store)
        self._waha_group_authors_to_store(store)

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

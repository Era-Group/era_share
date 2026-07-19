# Part of Era Group custom addons.
import logging

from odoo import models

_logger = logging.getLogger(__name__)


class DiscussChannelMember(models.Model):
    _inherit = 'discuss.channel.member'

    def _mark_as_read(self, *args, **kwargs):
        """When an agent reads a WAHA channel, tell WhatsApp so the customer gets blue
        (read) ticks. Best-effort: never let a WAHA hiccup break marking-as-read."""
        res = super()._mark_as_read(*args, **kwargs)
        for member in self:
            channel = member.channel_id
            account = channel.wa_account_id
            if channel.channel_type != 'whatsapp' or account.provider != 'waha':
                continue
            # Only agents send read receipts — never the customer's own partner.
            if member.partner_id and member.partner_id == channel.whatsapp_partner_id:
                continue
            try:
                account._waha_mark_seen(channel)
            except Exception:
                _logger.exception("WAHA sendSeen failed for channel %s", channel.id)
        return res

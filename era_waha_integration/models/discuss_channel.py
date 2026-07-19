# Part of Era Group custom addons.
import logging

from odoo import _, api, fields, models
from odoo.addons.mail.tools.discuss import Store
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


def _is_waha_channel(channel):
    return channel.channel_type == 'whatsapp' and channel.wa_account_id.provider == 'waha'


class DiscussChannel(models.Model):
    _inherit = 'discuss.channel'

    is_waha_channel = fields.Boolean(compute='_compute_is_waha_channel')

    @api.depends('channel_type', 'wa_account_id.provider')
    def _compute_is_waha_channel(self):
        for channel in self:
            channel.is_waha_channel = _is_waha_channel(channel)

    def _to_store_defaults(self, target):
        # Expose a flag so the Discuss sidebar can group WAHA channels in their own
        # category, separate from official (Meta) WhatsApp channels.
        return super()._to_store_defaults(target) + [
            Store.Attr("is_waha_channel", predicate=lambda c: c.channel_type == 'whatsapp'),
        ]

    @api.depends('wa_account_id.provider', 'last_wa_mail_message_id')
    def _compute_whatsapp_channel_valid_until(self):
        """WAHA has no 24h customer-service window. Return an empty validity so the Discuss
        composer stays enabled (composer_patch.js early-returns on a falsy value). A
        far-future datetime must NOT be used: it makes the composer schedule a clamped
        ~1ms setTimeout loop and spin the CPU."""
        waha = self.filtered(
            lambda c: c.channel_type == 'whatsapp' and c.wa_account_id.provider == 'waha')
        for channel in waha:
            channel.whatsapp_channel_valid_until = False
        super(DiscussChannel, self - waha)._compute_whatsapp_channel_valid_until()

    def _get_notify_valid_parameters(self):
        params = super()._get_notify_valid_parameters()
        if self.channel_type == 'whatsapp':
            return params | {'whatsapp_outbound_msg_uid'}
        return params

    def message_post(self, *args, **kwargs):
        # Enforce WAHA account-protection limits on genuine outbound sends (a reply
        # typed in Discuss, or a composer send). Inbound and history-import posts,
        # which carry a *_msg_uid, are exempt. Raising rolls back the request so the
        # message never appears as failed in the conversation.
        if (self.channel_type == 'whatsapp'
                and kwargs.get('message_type') == 'whatsapp_message'
                and self.wa_account_id.provider == 'waha'
                and not kwargs.get('whatsapp_inbound_msg_uid')
                and not kwargs.get('whatsapp_outbound_msg_uid')):
            author_pid = kwargs.get('author_id')
            user = self.env.user
            if author_pid and author_pid != user.partner_id.id:
                user = self.env['res.users'].sudo().search(
                    [('partner_id', '=', author_pid)], limit=1) or user
            self.wa_account_id._waha_check_send_allowed(
                self.whatsapp_number, user, channel=self, check_new=False)
        return super().message_post(*args, **kwargs)

    def _notify_thread(self, message, msg_vals=False, **kwargs):
        """History-import hook for outbound (fromMe) messages: create the outbound
        whatsapp.message BEFORE super posts, so message_post's 'if not new_msg.wa_message_ids'
        guard skips the actual send (mirrors the inbound whatsapp_inbound_msg_uid mechanism)."""
        wa_out_uid = kwargs.get('whatsapp_outbound_msg_uid')
        if wa_out_uid and self.channel_type == 'whatsapp':
            self.env['whatsapp.message'].sudo().create({
                'mail_message_id': message.id,
                'message_type': 'outbound',
                'state': 'sent',
                'msg_uid': wa_out_uid,
                'wa_account_id': self.wa_account_id.id,
                'mobile_number': ('+' + self.whatsapp_number) if self.whatsapp_number else False,
            })
        return super()._notify_thread(message, msg_vals=msg_vals, **kwargs)

    def action_waha_import_history(self):
        """Manually backfill the previous WAHA conversation into this channel."""
        for channel in self:
            if channel.channel_type != 'whatsapp' or channel.wa_account_id.provider != 'waha':
                raise UserError(_("This action is only available on WAHA WhatsApp channels."))
            channel.wa_account_id._waha_sync_channel_history(channel, deep=True)
        return True

    @api.model
    def get_whatsapp_channel_for_record(self, model, res_id):
        """Return the id of the most-recent whatsapp discuss.channel whose
        whatsapp_partner_id matches the partner of the given record, else False.
        Used by the floating WhatsApp window's chatter shortcut button."""
        if not res_id or not model:
            return False
        RecordModel = self.env.get(model)
        if RecordModel is None:
            return False
        if model == 'res.partner':
            partner_id = res_id
        elif 'partner_id' in RecordModel._fields:
            record = RecordModel.browse(res_id)
            partner_id = record.partner_id.id if record.partner_id else False
        else:
            return False
        if not partner_id:
            return False
        channel = self.search(
            [('channel_type', '=', 'whatsapp'), ('whatsapp_partner_id', '=', partner_id)],
            order='id desc', limit=1)
        return channel.id if channel else False

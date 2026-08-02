# Part of Era Group custom addons.
import logging

from odoo import _, api, fields, models
from odoo.addons.mail.tools.discuss import Store
from odoo.exceptions import AccessError, UserError, ValidationError

_logger = logging.getLogger(__name__)


def _is_waha_channel(channel):
    # Read `provider` via sudo: this predicate feeds computed fields
    # (is_waha_channel / whatsapp_channel_valid_until) that Odoo evaluates while
    # serializing the channel to EACH of its members. WhatsApp contacts are channel
    # members and are usually portal users, who have no read access to whatsapp.account
    # — a non-sudo read there raises AccessError and aborts the whole send/post.
    # `provider` is non-sensitive routing metadata, safe to expose to a channel member.
    return channel.channel_type == 'whatsapp' and channel.wa_account_id.sudo().provider == 'waha'


class DiscussChannel(models.Model):
    _inherit = 'discuss.channel'

    is_waha_channel = fields.Boolean(compute='_compute_is_waha_channel')
    waha_group_id = fields.Many2one('whatsapp.waha.group', ondelete='set null', index=True)
    waha_chat_id = fields.Char(copy=False, index=True)
    is_waha_group_channel = fields.Boolean(compute='_compute_is_waha_group_channel')
    # Channel members control access to every account conversation. Keep notification
    # ownership separate so Default Users do not receive every WAHA exchange forever.
    waha_participant_user_ids = fields.Many2many(
        'res.users', 'discuss_channel_waha_participant_rel', 'channel_id', 'user_id',
        string='WAHA Notification Participants', copy=False)

    @api.depends('channel_type', 'wa_account_id.provider')
    def _compute_is_waha_channel(self):
        for channel in self:
            channel.is_waha_channel = _is_waha_channel(channel)

    @api.depends('waha_group_id', 'waha_chat_id')
    def _compute_is_waha_group_channel(self):
        for channel in self:
            channel.is_waha_group_channel = bool(channel.waha_group_id or (channel.waha_chat_id or '').endswith('@g.us'))

    @api.constrains('channel_type', 'whatsapp_number', 'waha_chat_id', 'waha_group_id')
    def _check_waha_group_identifier(self):
        for channel in self.filtered(lambda c: c.channel_type == 'whatsapp' and c.is_waha_group_channel):
            if not channel.waha_chat_id or not channel.waha_chat_id.endswith('@g.us'):
                raise ValidationError(_('WAHA group channels require a valid group identifier.'))

    @api.constrains('channel_type', 'whatsapp_number')
    def _check_whatsapp_number(self):
        # Replace the standard phone-only constraint for WAHA group channels.
        normal = self.filtered(lambda c: not (c.channel_type == 'whatsapp' and c.is_waha_group_channel))
        if normal:
            return super(DiscussChannel, normal)._check_whatsapp_number()
        return True

    @api.depends('waha_group_id.subject', 'waha_chat_id', 'whatsapp_partner_id', 'whatsapp_number')
    def _compute_display_name(self):
        groups = self.filtered('is_waha_group_channel')
        for channel in groups:
            channel.display_name = channel.waha_group_id.subject or channel.name or channel.waha_chat_id
        super(DiscussChannel, self - groups)._compute_display_name()

    def _to_store_defaults(self, target):
        # Expose a flag so the Discuss sidebar can group WAHA channels in their own
        # category, separate from official (Meta) WhatsApp channels.
        return super()._to_store_defaults(target) + [
            Store.Attr("is_waha_channel", predicate=lambda c: c.channel_type == 'whatsapp'),
            Store.Attr("is_waha_group_channel", predicate=lambda c: c.channel_type == 'whatsapp'),
        ]

    @api.depends('wa_account_id.provider', 'last_wa_mail_message_id')
    def _compute_whatsapp_channel_valid_until(self):
        """WAHA has no 24h customer-service window. Return an empty validity so the Discuss
        composer stays enabled (composer_patch.js early-returns on a falsy value). A
        far-future datetime must NOT be used: it makes the composer schedule a clamped
        ~1ms setTimeout loop and spin the CPU."""
        waha = self.filtered(_is_waha_channel)
        for channel in waha:
            channel.whatsapp_channel_valid_until = False
        super(DiscussChannel, self - waha)._compute_whatsapp_channel_valid_until()

    def _get_notify_valid_parameters(self):
        params = super()._get_notify_valid_parameters()
        if self.channel_type == 'whatsapp':
            return params | {
                'whatsapp_outbound_msg_uid',
                'waha_internal_note',
                'waha_live_inbound',
            }
        return params

    def channel_pin(self, pinned=False):
        """Keep WAHA conversations visible instead of treating them as disposable chats."""
        waha_channels = self.filtered(_is_waha_channel)
        for channel in waha_channels:
            # The sidebar must retain conversation history. WAHA channels are an
            # inbox, not temporary direct messages that can disappear on unpin.
            super(DiscussChannel, channel).channel_pin(pinned=True)
        other_channels = self - waha_channels
        if other_channels:
            return super(DiscussChannel, other_channels).channel_pin(pinned=pinned)
        return True

    def message_post(self, *args, **kwargs):
        is_waha = _is_waha_channel(self)
        # The internal-note policy (an explicit note, or a mention of a colleague,
        # must never leave Odoo) applies to every WhatsApp channel regardless of
        # provider — official Meta accounts included, not only WAHA.
        is_whatsapp = self.channel_type == 'whatsapp'
        mentioned_partner_ids = []
        if is_whatsapp:
            mentioned_partner_ids = list(kwargs.get('partner_ids') or [])
            mentioned_partner_ids += list((kwargs.get('partner_ids_mention_token') or {}).keys())
        has_internal_mention = is_whatsapp and bool(self.env['res.partner'].browse(mentioned_partner_ids).filtered(
            lambda partner: partner.main_user_id and not partner.main_user_id.share))
        is_internal_note = kwargs.get('waha_internal_note') or has_internal_mention
        is_outbound = (
            is_waha
            and kwargs.get('message_type') == 'whatsapp_message'
            and not kwargs.get('whatsapp_inbound_msg_uid')
            and not kwargs.get('whatsapp_outbound_msg_uid')
        )
        participant_users = self.env['res.users']
        if is_waha and (is_outbound or is_internal_note):
            author_id = kwargs.get('author_id') or self.env.user.partner_id.id
            participant_users |= self._waha_internal_users_for_partners([author_id])
            # An internal-note mention explicitly hands the conversation to that
            # colleague, so subscribe them before this note is notified.
            if is_internal_note:
                participant_users |= self._waha_internal_users_for_partners(mentioned_partner_ids)
            if participant_users:
                self.waha_participant_user_ids |= participant_users
        if is_internal_note:
            if not is_whatsapp or not self.env.user._is_internal():
                raise AccessError(_('WhatsApp internal notes are restricted to internal users.'))
            # A standard internal log is deliberately not a whatsapp_message, so it
            # cannot create a whatsapp.message or reach the provider.
            kwargs['message_type'] = 'comment'
            kwargs['subtype_xmlid'] = 'mail.mt_note'
            kwargs.pop('waha_internal_note', None)
            message = super(DiscussChannel, self.with_context(waha_internal_note=True)).message_post(
                *args, **kwargs)
            if is_waha:
                self._waha_resolve_pending_replies(message)
            return message
        # Enforce WAHA account-protection limits on genuine outbound sends (a reply
        # typed in Discuss, or a composer send). Inbound and history-import posts,
        # which carry a *_msg_uid, are exempt. Raising rolls back the request so the
        # message never appears as failed in the conversation.
        if (self.channel_type == 'whatsapp'
                and kwargs.get('message_type') == 'whatsapp_message'
                and self.wa_account_id.provider == 'waha'
                and not kwargs.get('whatsapp_inbound_msg_uid')
                and not kwargs.get('whatsapp_outbound_msg_uid')
                and not self.is_waha_group_channel):
            author_pid = kwargs.get('author_id')
            user = self.env.user
            if author_pid and author_pid != user.partner_id.id:
                user = self.env['res.users'].sudo().search(
                    [('partner_id', '=', author_pid)], limit=1) or user
            # check_new=True: a first contact typed straight into Discuss is exactly the
            # cold outreach the daily cap exists to bound. Exempting this path left the
            # cap enforceable only from the composers, which is not where most sends
            # actually originate.
            self.wa_account_id._waha_check_send_allowed(
                self.whatsapp_number, user, channel=self, check_new=True)
        message = super().message_post(*args, **kwargs)
        if is_outbound:
            self._waha_resolve_pending_replies(message)
        return message

    def _waha_internal_users_for_partners(self, partner_ids):
        """Return active internal users represented by the given partners."""
        partners = self.env['res.partner'].browse(partner_ids).exists()
        return self.env['res.users'].sudo().search([
            ('partner_id', 'in', partners.ids),
            ('active', '=', True),
            ('share', '=', False),
        ])

    def _waha_notification_users(self):
        self.ensure_one()
        return self.waha_participant_user_ids.filtered(
            lambda user: user.active and not user.share)

    def _waha_filter_recipients(self, recipients_data, users, author_id=False):
        """Keep stock Discuss notification preferences while limiting recipients."""
        partner_ids = set(users.partner_id.ids)
        if author_id:
            partner_ids.discard(author_id)
        seen = set()
        return [
            recipient for recipient in recipients_data
            if recipient['id'] in partner_ids
            and not (recipient['id'] in seen or seen.add(recipient['id']))
        ]

    def _waha_recipients_for_users(self, message, users, msg_vals=False):
        """Compute normal Discuss recipient data, then restrict it to WAHA owners."""
        self.ensure_one()
        recipients_data = super()._notify_get_recipients(message, msg_vals=msg_vals or {})
        author_id = (msg_vals or {}).get('author_id') or message.author_id.id
        return self._waha_filter_recipients(recipients_data, users, author_id=author_id)

    def _waha_notification_users_for_message(self, message, msg_vals=False, **kwargs):
        """Return WAHA users entitled to interrupt/mark unread for this message."""
        msg_vals = msg_vals or {}
        message_type = msg_vals.get('message_type', message.message_type)
        if message_type == 'whatsapp_message':
            if (kwargs.get('whatsapp_inbound_msg_uid')
                    and kwargs.get('waha_live_inbound')):
                return self._waha_notification_users() or self.wa_account_id.notify_user_ids.filtered(
                    lambda user: user.active and not user.share)
            return self.env['res.users']
        if message_type == 'comment' and self._waha_is_internal_note(message, msg_vals):
            return self._waha_notification_users()
        return self.env['res.users']

    def _waha_is_internal_note(self, message, msg_vals=False):
        subtype_id = (msg_vals or {}).get('subtype_id') or message.subtype_id.id
        return subtype_id == self.env.ref('mail.mt_note').id

    def _notify_get_recipients(self, message, msg_vals=False, **kwargs):
        recipients_data = super()._notify_get_recipients(message, msg_vals=msg_vals, **kwargs)
        if not _is_waha_channel(self):
            return recipients_data

        msg_vals = msg_vals or {}
        message_type = msg_vals.get('message_type', message.message_type)
        if message_type == 'whatsapp_message':
            # A WAHA outgoing message is already visible in the channel through the
            # normal bus event, but it must never interrupt colleagues with a push.
            if not kwargs.get('whatsapp_inbound_msg_uid'):
                return []
            # Initial history and manual/reconciliation imports intentionally remain
            # quiet. Only a live webhook starts a fresh notification lifecycle.
            if not kwargs.get('waha_live_inbound'):
                return []
            users = self._waha_notification_users_for_message(message, msg_vals, **kwargs)
            return self._waha_filter_recipients(
                recipients_data, users,
                author_id=msg_vals.get('author_id') or message.author_id.id)

        if message_type == 'comment' and self._waha_is_internal_note(message, msg_vals):
            return self._waha_filter_recipients(
                recipients_data, self._waha_notification_users(),
                author_id=msg_vals.get('author_id') or message.author_id.id)
        return recipients_data

    def _waha_send_unread_state(self, members):
        """Refresh another member's sidebar counter after changing its separator."""
        for member in members:
            bus_last_id = self.env['bus.bus'].sudo()._bus_last_id()
            Store(bus_channel=member._bus_channel()).add(
                member,
                [
                    Store.One('channel_id', [], as_thread=True),
                    'message_unread_counter',
                    {'message_unread_counter_bus_id': bus_last_id},
                    'new_message_separator',
                    *self.env['discuss.channel.member']._to_store_persona([]),
                ],
            ).bus_send()

    def _waha_mark_nonrecipients_as_read(self, message, recipient_partner_ids, notify=True):
        """WAHA channel membership grants access, not a permanent unread subscription."""
        self.ensure_one()
        members = self.env['discuss.channel.member'].sudo().search([
            ('channel_id', '=', self.id),
            ('partner_id.user_ids.share', '=', False),
            ('partner_id', 'not in', recipient_partner_ids),
        ])
        for member in members:
            member._set_new_message_separator(message.id + 1)
        if notify:
            self._waha_send_unread_state(members)

    def _waha_backfill_participants(self):
        """Recover notification ownership from prior internal WAHA activity.

        This changes no message, notification, or escalation state. It only records
        the people who already handled a conversation before this policy existed.
        """
        self.ensure_one()
        messages = self.env['mail.message'].sudo().search([
            ('model', '=', 'discuss.channel'),
            ('res_id', '=', self.id),
            '|',
            ('message_type', '=', 'whatsapp_message'),
            ('subtype_id', '=', self.env.ref('mail.mt_note').id),
        ])
        users = self._waha_internal_users_for_partners(messages.author_id.ids)
        if users:
            self.waha_participant_user_ids |= users
        return self._waha_notification_users()

    def _waha_resolve_pending_replies(self, handled_message):
        """A human reply resolves the inbox item for every internal channel member."""
        self.ensure_one()
        self.env['mail.message'].sudo().search([
            ('model', '=', 'discuss.channel'),
            ('res_id', '=', self.id),
            ('waha_pending_reply', '=', True),
        ]).write({'waha_pending_reply': False})
        members = self.env['discuss.channel.member'].sudo().search([
            ('channel_id', '=', self.id),
            ('partner_id.user_ids.share', '=', False),
        ])
        for member in members:
            member._set_new_message_separator(handled_message.id + 1)
        # `_set_new_message_separator` updates the server-side counter but does
        # not notify another user's browser. Refresh all sidebars immediately.
        self._waha_send_unread_state(members)

    def _message_post_after_hook(self, message, msg_vals):
        return super()._message_post_after_hook(message, msg_vals)

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
                'waha_chat_id': self.waha_chat_id or False,
            })
        recipient_partner_ids = []
        if _is_waha_channel(self):
            users = self._waha_notification_users_for_message(message, msg_vals, **kwargs)
            recipients_data = self._waha_recipients_for_users(message, users, msg_vals)
            recipient_partner_ids = [recipient['id'] for recipient in recipients_data]
            message.sudo().write({
                'waha_notification_policy_applied': True,
                'waha_notification_partner_ids': [(6, 0, recipient_partner_ids)],
            })
        if kwargs.get('waha_live_inbound') and self.channel_type == 'whatsapp':
            message.sudo().write({
                'waha_pending_reply': True,
                'waha_escalated_at': False,
            })
        result = super()._notify_thread(message, msg_vals=msg_vals, **kwargs)
        if _is_waha_channel(self):
            self._waha_mark_nonrecipients_as_read(message, recipient_partner_ids)
        return result

    def _get_inbound_whatsapp_message_values_from_mail_message(self, mail_message, whatsapp_message_uid, parent_msg_id=False):
        vals = super()._get_inbound_whatsapp_message_values_from_mail_message(
            mail_message, whatsapp_message_uid, parent_msg_id)
        if self.is_waha_group_channel:
            vals['mobile_number'] = False
            vals['waha_chat_id'] = self.waha_chat_id
        return vals

    def _get_outbound_whatsapp_message_values_from_mail_message(self, mail_message):
        vals = super()._get_outbound_whatsapp_message_values_from_mail_message(mail_message)
        if self.is_waha_group_channel:
            vals['mobile_number'] = False
            vals['waha_chat_id'] = self.waha_chat_id
        return vals

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

    @api.model
    def _waha_get_group_channel(self, account, group, create_if_not_found=False):
        channel = self.sudo().search([
            ('channel_type', '=', 'whatsapp'),
            ('wa_account_id', '=', account.id),
            ('waha_chat_id', '=', group.chat_id),
        ], limit=1)
        if channel or not create_if_not_found:
            return channel
        channel = self.sudo().create({
            'name': group.subject or group.chat_id,
            'channel_type': 'whatsapp',
            'wa_account_id': account.id,
            'waha_group_id': group.id,
            'waha_chat_id': group.chat_id,
        })
        account._waha_grant_members(channel)
        partners = account.notify_user_ids.partner_id
        if partners:
            channel._broadcast(partners.ids)
        return channel

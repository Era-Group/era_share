# Part of Era Group custom addons.
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class DiscussChannel(models.Model):
    _inherit = 'discuss.channel'

    wag_group_id = fields.Many2one(
        'whatsapp.cloud.group', string='Cloud API Group',
        index='btree_not_null', ondelete='set null', copy=False)
    wag_group_uid = fields.Char(
        related='wag_group_id.group_uid', store=True, index='btree_not_null',
        string='Cloud API Group ID',
        help="Stored so channel lookup on an inbound webhook is one indexed read.")

    # ------------------------------------------------------------------
    # Constraints
    # ------------------------------------------------------------------
    @api.model
    def _check_whatsapp_number_contains_fields(self):
        """Add our discriminator to the constraint's trigger list.

        Upstream declares the constraint as
        `@api.constrains(lambda self: self._check_whatsapp_number_contains_fields())`
        (whatsapp/models/discuss_channel.py:56) precisely so downstream modules can
        widen it here instead of re-decorating.

        'channel_type' is re-added explicitly: whatsapp_identifiers REPLACES the
        list rather than extending it, dropping base's ['channel_type',
        'whatsapp_number']. Our exemption is keyed on channel_type, so a record
        turning into a whatsapp channel has to re-trigger validation or a
        numberless non-group channel could slip in unchecked.
        """
        fields_list = super()._check_whatsapp_number_contains_fields()
        for name in ('channel_type', 'wag_group_uid'):
            if name not in fields_list:
                fields_list = fields_list + [name]
        return fields_list

    @api.constrains(lambda self: self.env['discuss.channel']._check_whatsapp_number_contains_fields())
    def _check_whatsapp_number(self):
        """Exempt group channels from the phone-number requirement.

        The decorator MUST be repeated here. Odoo collects constraints with
        `getmembers(cls, hasattr '_constrains')` on the most-derived attribute
        (odoo/orm/models.py:519-545), and a plain override carries no `_constrains`
        attribute -- so overriding without re-declaring does not relax the
        constraint, it DELETES it for every channel. (Stock `whatsapp_identifiers`
        has exactly that bug; it is uninstalled here, which is the only reason the
        constraint still fires at all.)

        Filter-mine-and-super, so this composes with era_waha_integration's
        equivalent override whichever of the two ends up outermost.
        """
        others = self.filtered(lambda c: not (c.channel_type == 'whatsapp' and c.wag_group_id))
        if others:
            return super(DiscussChannel, others)._check_whatsapp_number()
        return True

    # ------------------------------------------------------------------
    # Group channels
    # ------------------------------------------------------------------
    @api.model
    def _wag_get_group_channel(self, account, group, create_if_not_found=False):
        """Return (and optionally create) the Discuss channel backing a group."""
        channel = self.sudo().search([
            ('wa_account_id', '=', account.id),
            ('wag_group_uid', '=', group.group_uid),
        ], limit=1)
        if channel:
            if group.subject and channel.name != group.subject:
                channel.sudo().name = group.subject
            if group.channel_id != channel:
                group.sudo().channel_id = channel.id
            return channel
        if not create_if_not_found:
            return self.env['discuss.channel']
        channel = self.sudo().create({
            'name': group.subject or group.group_uid,
            'channel_type': 'whatsapp',
            'wa_account_id': account.id,
            'wag_group_id': group.id,
            # No whatsapp_number and no whatsapp_partner_id: a group has neither.
            # The constraint above is what makes that legal.
            'whatsapp_number': False,
            'channel_member_ids': [
                (0, 0, {'partner_id': partner.id})
                for partner in account.notify_user_ids.partner_id
            ],
        })
        group.sudo().channel_id = channel.id
        return channel

    def message_post(self, **kwargs):
        """Attribute an inbound group message to the participant who sent it.

        Upstream posts with `author_id = channel.whatsapp_partner_id`, which is
        empty for a group. `whatsapp.account._process_messages` resolves the real
        sender and passes it in the context; without this the whole group history
        would be authored by nobody.
        """
        author_id = self.env.context.get('wag_group_author_id')
        if author_id and self.wag_group_id and not kwargs.get('author_id'):
            kwargs['author_id'] = author_id
        return super().message_post(**kwargs)

    # ------------------------------------------------------------------
    # Outbound plumbing
    # ------------------------------------------------------------------
    def _get_outbound_whatsapp_message_values_from_mail_message(self, mail_message):
        vals = super()._get_outbound_whatsapp_message_values_from_mail_message(mail_message)
        if self.wag_group_id:
            vals['mobile_number'] = False
            vals['wag_group_id'] = self.wag_group_id.id
        return vals

    def _get_inbound_whatsapp_message_values_from_mail_message(self, mail_message,
                                                               whatsapp_message_uid,
                                                               parent_msg_id=False):
        vals = super()._get_inbound_whatsapp_message_values_from_mail_message(
            mail_message, whatsapp_message_uid, parent_msg_id)
        if self.wag_group_id:
            vals['mobile_number'] = False
            vals['wag_group_id'] = self.wag_group_id.id
        return vals

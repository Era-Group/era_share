# Part of Era Group custom addons.
import logging

from odoo import _, fields, models
from odoo.exceptions import UserError

from odoo.addons.era_whatsapp_groups.tools.whatsapp_group_api import WhatsAppGroupApi

_logger = logging.getLogger(__name__)


def wag_is_cloud_account(account):
    """True when this account is served by Meta's Cloud API.

    `provider` is added by era_waha_integration, which this module does NOT
    depend on -- the two are siblings, either can be installed alone. Feature-
    detect the field instead of importing it: with era_waha_integration absent
    there is only one provider and every account is Cloud API.

    sudo() because a portal channel member can serialize a channel without
    holding read access on whatsapp.account.
    """
    if not account:
        return False
    if 'provider' not in account._fields:
        return True
    return account.sudo().provider == 'meta'


class WhatsappAccount(models.Model):
    _inherit = 'whatsapp.account'

    wag_group_ids = fields.One2many(
        'whatsapp.cloud.group', 'account_id', string='Cloud API Groups')
    wag_group_count = fields.Integer(compute='_compute_wag_group_count')

    def _compute_wag_group_count(self):
        counts = dict(self.env['whatsapp.cloud.group']._read_group(
            [('account_id', 'in', self.ids)], ['account_id'], ['__count']))
        for account in self:
            account.wag_group_count = counts.get(account, 0)

    # ------------------------------------------------------------------
    # Transport
    # ------------------------------------------------------------------
    def _wag_api(self):
        self.ensure_one()
        if not wag_is_cloud_account(self):
            raise UserError(_('Group management over the Cloud API is only available on Meta accounts.'))
        return WhatsAppGroupApi(self)

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------
    def action_wag_sync_groups(self):
        """Reconcile the local registry with what Meta reports.

        Never enables a group. Discovery and authorisation are separate decisions:
        an inbound group message can create a Discuss channel and pull an outside
        conversation into the company's record, so a human approves each group.

        A group that disappears is marked unavailable rather than unlinked, so the
        operator's enable/disable decision and the channel link survive a blip.
        """
        self.ensure_one()
        api = self._wag_api()
        Group = self.env['whatsapp.cloud.group'].sudo()
        seen, after = set(), None
        while True:
            payload = api._wag_list_groups(after=after) or {}
            rows = payload.get('data') or []
            for raw in rows:
                group_uid = (raw or {}).get('id')
                if not group_uid:
                    continue
                seen.add(group_uid)
                vals = {
                    'subject': raw.get('subject') or raw.get('name') or group_uid,
                    'available': True,
                    'last_sync_at': fields.Datetime.now(),
                }
                if 'participant_count' in raw:
                    vals['participant_count'] = raw['participant_count'] or 0
                elif isinstance(raw.get('participants'), list):
                    vals['participant_count'] = len(raw['participants'])
                group = Group.search(
                    [('account_id', '=', self.id), ('group_uid', '=', group_uid)], limit=1)
                if group:
                    group.write(vals)
                else:
                    Group.create(vals | {'account_id': self.id,
                                         'group_uid': group_uid,
                                         'enabled': False})
            after = ((payload.get('paging') or {}).get('cursors') or {}).get('after')
            # Stop on the last page. Meta echoes the same cursor on the final page,
            # so guard on the row count too or this loops forever.
            if not after or not rows:
                break
        missing = Group.search([('account_id', '=', self.id), ('group_uid', 'not in', list(seen))])
        missing.write({'available': False, 'last_sync_at': fields.Datetime.now()})
        return True

    def _cron_wag_sync_groups(self):
        for account in self.search([]):
            if not wag_is_cloud_account(account):
                continue
            try:
                account.action_wag_sync_groups()
            except Exception:
                _logger.exception('WhatsApp groups: sync failed for account %s', account.id)

    # ------------------------------------------------------------------
    # Inbound
    # ------------------------------------------------------------------
    def _wag_group_uid_from_payload(self, message_values):
        """Group id carried by an inbound message webhook, or ''.

        UNVERIFIED shape -- see tools/whatsapp_group_api.py. Meta's own docs are
        inconsistent enough elsewhere (recipient_participant_id vs
        participant_recipient_id in status webhooks) that accepting several
        spellings costs nothing and avoids a silent drop, which is the worst
        outcome here: an unrecognised inbound group message would otherwise be
        routed to a 1:1 channel keyed on the participant's number.
        """
        if not isinstance(message_values, dict):
            return ''
        for key in ('group_id', 'group', 'recipient_group_id'):
            value = message_values.get(key)
            if isinstance(value, str) and value:
                return value
            if isinstance(value, dict):
                nested = value.get('id')
                if isinstance(nested, str) and nested:
                    return nested
        return ''

    def _wag_enabled_group(self, group_uid):
        return self.env['whatsapp.cloud.group'].sudo().search([
            ('account_id', '=', self.id), ('group_uid', '=', group_uid),
            ('enabled', '=', True), ('available', '=', True)], limit=1)

    def _find_active_channel_from_whatsapp_message_values(self, whatsapp_message_values,
                                                          whatsapp_contacts_values,
                                                          create_if_not_found=False):
        """Route a group-addressed inbound message to its group channel.

        Overriding here rather than in `_process_messages` keeps upstream's whole
        message-body pipeline (text, media, location, contacts, reactions) intact
        -- we only change WHICH channel it lands in.

        Falls through to super() for everything that is not an enabled group, so
        WAHA accounts and ordinary 1:1 Cloud API traffic are untouched.
        """
        group_uid = self._wag_group_uid_from_payload(whatsapp_message_values)
        if group_uid and wag_is_cloud_account(self):
            group = self._wag_enabled_group(group_uid)
            if not group:
                # Known-but-disabled, or entirely unknown: drop rather than open a
                # channel. Silence is the safe default for an unapproved group.
                _logger.info(
                    'WhatsApp groups: ignoring inbound message for unapproved group %s on account %s',
                    group_uid, self.id)
                return self.env['discuss.channel']
            return self.env['discuss.channel']._wag_get_group_channel(
                self, group, create_if_not_found=create_if_not_found)
        return super()._find_active_channel_from_whatsapp_message_values(
            whatsapp_message_values, whatsapp_contacts_values,
            create_if_not_found=create_if_not_found)

    def _process_messages(self, value):
        """Carry the real sender into message_post for group messages.

        Upstream posts every inbound message with `author_id =
        channel.whatsapp_partner_id` (whatsapp_account.py:283). A group has no
        single counterpart, so without this every message in the channel would be
        attributed to whichever partner happened to create it. The sender is
        resolved here and picked up by discuss.channel.message_post.
        """
        if not wag_is_cloud_account(self):
            return super()._process_messages(value)
        if 'messages' not in value and value.get('whatsapp_business_api_data', {}).get('messages'):
            value = value['whatsapp_business_api_data']
        messages = value.get('messages') or []
        group_messages = [m for m in messages if self._wag_group_uid_from_payload(m)]
        if not group_messages:
            return super()._process_messages(value)

        # Process group messages one at a time so each carries its own author.
        for message in messages:
            single = dict(value, messages=[message])
            account = self
            group_uid = self._wag_group_uid_from_payload(message)
            if group_uid:
                partner = self._wag_resolve_group_sender(message, value.get('contacts'))
                if partner:
                    account = self.with_context(wag_group_author_id=partner.id)
            super(WhatsappAccount, account)._process_messages(single)
        return True

    def _wag_resolve_group_sender(self, message_values, contacts_values):
        """Partner behind an individual message inside a group, or an empty recordset."""
        self.ensure_one()
        from_number = message_values.get('from')
        if not from_number:
            return self.env['res.partner']
        formatted = self._format_incoming_from_number(from_number)
        if not formatted:
            return self.env['res.partner']
        sender_name = (contacts_values or [{}])[0].get('profile', {}).get('name')
        return self.env['res.partner']._find_or_create_from_number(
            formatted, name=sender_name) if hasattr(
                self.env['res.partner'], '_find_or_create_from_number'
        ) else self.env['res.partner'].sudo().search(
            [('phone', '=', formatted)], limit=1)

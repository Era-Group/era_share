# Part of Era Group custom addons.
from odoo import fields, models

from odoo.addons.era_whatsapp_groups.models.whatsapp_account import wag_is_cloud_account
from odoo.addons.era_whatsapp_groups.tools.whatsapp_group_api import WhatsAppGroupApi


class WhatsappMessage(models.Model):
    _inherit = 'whatsapp.message'

    wag_group_id = fields.Many2one(
        'whatsapp.cloud.group', string='Cloud API Group',
        index='btree_not_null', ondelete='set null', copy=False,
        help="Set when this message is addressed to a group rather than a single number.")

    def _assert_recipient_identifier(self):
        """A group message legitimately has no phone number.

        Upstream raises `phone_invalid` whenever `mobile_number_formatted` is
        empty (whatsapp_message.py:239-242). For a group the recipient IS the
        group id, so return that instead of letting a valid send die on a
        number check that does not apply to it.
        """
        if self.wag_group_id:
            return self.wag_group_id.group_uid
        return super()._assert_recipient_identifier()

    def _send_with_identifier(self, wa_api, **send_kwargs):
        """Send to a group instead of a number.

        THIS, not `_send_message`, is the seam -- deliberately.
        era_waha_integration overrides `_send_message` and finishes with
        `super(WhatsappMessage, meta)._send_message(...)`, binding super to ITS
        own class. Because this module loads after it, our class sits EARLIER in
        the MRO, so that call resolves to the class after WAHA's and would skip
        an override of ours entirely. `_send_with_identifier` is reached per
        record from inside base `_send_message`, well after WAHA has filtered
        its own records out, so it runs for exactly the messages we own.
        """
        if self.wag_group_id and wag_is_cloud_account(self.wa_account_id):
            group_api = WhatsAppGroupApi(self.wa_account_id)
            return group_api._wag_send_to_group(self.wag_group_id.group_uid, **send_kwargs)
        return super()._send_with_identifier(wa_api, **send_kwargs)

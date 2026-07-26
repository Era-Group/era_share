# Part of Era Group custom addons.
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class WahaGroup(models.Model):
    _name = 'whatsapp.waha.group'
    _description = 'WAHA WhatsApp Group Allowlist'
    _order = 'subject, chat_id'

    account_id = fields.Many2one('whatsapp.account', required=True, ondelete='cascade', index=True)
    chat_id = fields.Char(required=True, readonly=True, index=True, copy=False)
    subject = fields.Char(string='Group Name')
    enabled = fields.Boolean(help='Only enabled groups may create or receive Discuss messages.')
    available = fields.Boolean(default=True, readonly=True)
    last_sync_at = fields.Datetime(readonly=True, copy=False)

    _unique_account_chat = models.Constraint(
        'unique(account_id, chat_id)', 'A WAHA group can only be listed once per account.')

    @api.constrains('chat_id')
    def _check_chat_id(self):
        for group in self:
            if not group.chat_id or not group.chat_id.endswith('@g.us'):
                raise ValidationError(_('WAHA group identifiers must end with @g.us.'))

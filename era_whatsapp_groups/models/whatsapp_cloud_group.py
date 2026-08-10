# Part of Era Group custom addons.
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

# Meta's hard cap. Duplicated from the transport layer as a plain int rather than
# imported, so the model stays readable and the tools package stays optional.
WAG_MAX_PARTICIPANTS = 8


class WhatsappCloudGroup(models.Model):
    """A WhatsApp group reachable through the official Meta Cloud API.

    Named `whatsapp.cloud.group`, NOT `whatsapp.group`: Meta shipped the Groups
    API in May 2026 and Odoo may well add a model under the obvious name. Two
    modules declaring the same _name silently merge into one table with each
    other's fields, which is far worse to unpick than a slightly longer name.
    """
    _name = 'whatsapp.cloud.group'
    _description = 'WhatsApp Cloud API Group'
    _order = 'subject, group_uid'

    account_id = fields.Many2one(
        'whatsapp.account', required=True, ondelete='cascade', index=True)
    group_uid = fields.Char(
        string='Group ID', required=True, readonly=True, index=True, copy=False,
        help="Identifier Meta assigns to the group. Opaque - never derived from a phone number.")
    subject = fields.Char(string='Group Name')
    enabled = fields.Boolean(
        help="Only enabled groups may create or receive Discuss messages. Sync discovers "
             "groups; it never enables one. An operator decides which groups Odoo joins in on.")
    available = fields.Boolean(
        default=True, readonly=True,
        help="Cleared when a sync no longer sees the group - the business was removed from it, "
             "or it was deleted. Kept rather than unlinked so history and the operator's "
             "enable/disable decision survive.")
    participant_count = fields.Integer(readonly=True)
    last_sync_at = fields.Datetime(readonly=True, copy=False)
    channel_id = fields.Many2one(
        'discuss.channel', string='Discuss Channel', readonly=True,
        ondelete='set null', index='btree_not_null', copy=False)

    _unique_account_group = models.Constraint(
        'unique(account_id, group_uid)',
        'A WhatsApp group can only be listed once per account.')

    @api.constrains('participant_count')
    def _check_participant_count(self):
        """Surface Meta's cap as data, not as a surprise at send time.

        This is a warning-shaped constraint on purpose: we only ever WRITE this
        field from what Meta reports, so a value above the cap means Meta changed
        the limit, not that we built something invalid. Raising here would make
        the module unable to sync its own groups the day that happens.
        """
        return True

    @api.depends('subject', 'group_uid')
    def _compute_display_name(self):
        for group in self:
            group.display_name = group.subject or group.group_uid or _('Unnamed group')

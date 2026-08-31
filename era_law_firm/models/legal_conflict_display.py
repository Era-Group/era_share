"""What a conflict check looks like from the outside.

The check listed as a bare row of ids told a supervisor nothing: which case,
whose client, how bad, and how recent are exactly the four things they open the
list to learn. These are display fields — related or computed, nothing stored
that the check itself must maintain — so the register reads without opening
every record.
"""
from odoo import api, fields, models

# Strongest first: the same contact record is certainty, a shared ID is nearly
# that, a name match is a question for a human.
BASIS_RANK = ('same_partner', 'identity_number', 'normalised_name')


class LegalConflictCheck(models.Model):
    _inherit = 'legal.conflict.check'
    _order = 'checked_on desc, id desc'

    client_id = fields.Many2one(related='case_id.client_id', store=True, string='Client')
    lawyer_id = fields.Many2one(related='case_id.lawyer_id', store=True, string='Lawyer')
    case_state = fields.Selection(related='case_id.state', string='Case Status')
    checked_on = fields.Datetime(string='Last Run', readonly=True, copy=False)
    # Stored, not because the value is expensive but because a register you
    # cannot filter, sort or group by is a wall of rows. The selection is
    # written out rather than borrowed from the line model: a stored column
    # needs a stable definition, and these three values are the vocabulary.
    match_count = fields.Integer(
        compute='_compute_match_summary', store=True, string='Matches')
    strongest_basis = fields.Selection([
        ('same_partner', 'Same contact record'),
        ('identity_number', 'Same ID / registration number'),
        ('normalised_name', 'Same name (different record)'),
    ], compute='_compute_match_summary', store=True, string='Strongest Match')

    @api.depends('line_ids.match_basis')
    def _compute_match_summary(self):
        for record in self:
            record.match_count = len(record.line_ids)
            found = set(record.line_ids.mapped('match_basis'))
            record.strongest_basis = next((b for b in BASIS_RANK if b in found), False)

    @api.depends('case_id.name', 'state')
    def _compute_display_name(self):
        # A conflict check referenced anywhere — the case form, the dashboard
        # list, a log note — should say which case it belongs to.
        labels = dict(self._fields['state'].selection)
        for record in self:
            case = record.case_id.name or record.env._('New Case')
            record.display_name = '%s — %s' % (case, labels.get(record.state, ''))

    def action_run_check(self):
        result = super().action_run_check()
        # sudo: a lawyer may run the check but does not own the register.
        self.sudo().write({'checked_on': fields.Datetime.now()})
        return result

"""Open a case the way a firm actually opens one.

The case form asks for everything at once and enforces the order afterwards:
create, then remember to add the parties, then remember to run the conflict
check, then discover on confirming that one of them was missed. Every step is
guarded, and none of them is offered.

This asks the four things intake actually establishes — who the client is,
what the matter is, who is against them, and who is running it — then does the
rest in the order the rules require, and reports what it found. Nothing here
bypasses a gate: it runs the same conflict check and stops at the same
blocked result.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class LegalIntakeWizard(models.TransientModel):
    _name = 'legal.intake.wizard'
    _description = 'Open a Case'

    client_id = fields.Many2one(
        'res.partner', string='Client', required=True,
        help="Who the firm is acting for. Their other cases are what the "
             "conflict check compares against.")
    name = fields.Char(
        string='Matter', help="What this file is about, in the firm's own words. "
                              "Left blank, the case takes its reference number.")
    case_type = fields.Selection(
        selection='_case_type_selection', string='Type', required=True)
    lawyer_id = fields.Many2one(
        'res.users', string='Responsible Lawyer', required=True,
        default=lambda self: self.env.user)
    opponent_ids = fields.Many2many(
        'res.partner', 'legal_intake_opponent_rel', 'wizard_id', 'partner_id',
        string='Opposing Parties',
        help="Anyone on the other side. Naming them here is what makes the "
             "conflict check meaningful — a check run before the opponents are "
             "known has almost nothing to compare.")
    engagement_type = fields.Selection(
        [('none', 'Not yet'), ('hourly', 'Hourly'), ('fixed', 'Fixed fee')],
        string='Fee Arrangement', default='none', required=True)
    hourly_rate = fields.Monetary(string='Hourly Rate', currency_field='currency_id')
    fixed_amount = fields.Monetary(string='Fixed Fee', currency_field='currency_id')
    currency_id = fields.Many2one(
        'res.currency', default=lambda self: self.env.company.currency_id)

    @api.model
    def _case_type_selection(self):
        """Borrow the case's own list, with its labels translated.

        Reading ``_fields['case_type'].selection`` gives the raw tuples defined
        in Python: Odoo keeps selection labels in ir.model.fields.selection and
        applies the language there, so the wizard showed English in an Arabic
        form. fields_get resolves them for the active language, and borrowing
        rather than restating keeps the two lists from drifting apart.
        """
        return self.env['legal.case'].fields_get(
            ['case_type'])['case_type']['selection']

    conflict_preview = fields.Html(
        string='Conflict', compute='_compute_conflict_preview',
        help="What a conflict check would find with the parties named so far, "
             "shown before the case exists rather than after it is refused.")

    @api.depends('client_id', 'opponent_ids')
    def _compute_conflict_preview(self):
        for wizard in self:
            wizard.conflict_preview = wizard._render_preview(
                wizard._conflict_hits(), named=bool(wizard.opponent_ids))

    def _conflict_hits(self):
        """The same three keys the real check uses, run against draft parties."""
        self.ensure_one()
        partners = self.client_id | self.opponent_ids
        if not partners:
            return []
        Check = self.env['legal.conflict.check']
        probe = Check.new({'company_id': self.env.company.id})
        candidates = self.env['legal.case.party'].search([
            ('company_id', '=', self.env.company.id),
            ('case_id.state', 'in', ('confirmed', 'closed')),
        ])
        hits = []
        for candidate in candidates:
            basis = probe._conflict_basis(partners, candidate.partner_id)
            if basis:
                hits.append((candidate.partner_id, candidate.case_id, basis))
        return hits

    @api.model
    def _render_preview(self, hits, named=False):
        if not hits:
            # "Nothing found" and "nothing to look for" are different answers,
            # and showing the second when the first is true reads as a system
            # that has not run rather than one that came back clear.
            return '<span class="%s">%s</span>' % (
                ('text-success' if named else 'text-muted'),
                _('No existing file shares a party with this one.') if named else
                _('Name the opposing parties — a check run before they are known '
                  'has almost nothing to compare.'))
        # fields_get, not _fields[...].selection: the raw tuples carry the
        # English defined in Python, and this text is shown to the lawyer.
        labels = dict(self.env['legal.conflict.check.line'].fields_get(
            ['match_basis'])['match_basis']['selection'])
        rows = ''.join(
            '<li>%s — %s <span class="text-muted">(%s)</span></li>'
            % (partner.display_name, case.display_name, labels.get(basis, basis))
            for partner, case, basis in hits)
        return _(
            '<div class="text-danger"><strong>%(count)s existing file(s) share a '
            'party with this one.</strong> The case will open, but it cannot be '
            'confirmed until a manager records a reason.</div><ul>%(rows)s</ul>',
            count=len(hits), rows=rows)

    def action_open_case(self):
        """Create the file, add the parties, run the check — in that order."""
        self.ensure_one()
        if self.engagement_type == 'hourly' and not self.hourly_rate:
            raise UserError(_('An hourly engagement needs a rate.'))
        if self.engagement_type == 'fixed' and not self.fixed_amount:
            raise UserError(_('A fixed-fee engagement needs an amount.'))

        case = self.env['legal.case'].create({
            'name': self.name or _('New Matter'),
            'client_id': self.client_id.id,
            'lawyer_id': self.lawyer_id.id,
            'case_type': self.case_type,
            'company_id': self.env.company.id,
        })
        for opponent in self.opponent_ids:
            self.env['legal.case.party'].create({
                'case_id': case.id, 'partner_id': opponent.id,
                'role': 'opponent', 'company_id': case.company_id.id})

        # The parties have to exist before the check, or it compares nothing.
        check = self.env['legal.conflict.check'].create({
            'case_id': case.id, 'company_id': case.company_id.id})
        check.action_run_check()

        if self.engagement_type != 'none':
            self.env['legal.engagement'].create(dict(
                self.env['legal.engagement'].default_get(['product_id']),
                case_id=case.id,
                name=_('Fee agreement'),
                billing_type='hourly' if self.engagement_type == 'hourly' else 'fixed',
                hourly_rate=self.hourly_rate,
                amount=self.fixed_amount,
            )).action_activate()

        if check.state == 'clear':
            case.action_confirm()

        return {
            'type': 'ir.actions.act_window',
            'res_model': 'legal.case',
            'res_id': case.id,
            'view_mode': 'form',
            'target': 'current',
        }

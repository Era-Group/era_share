"""The landing page: what needs each role's attention, before they go looking.

A TransientModel opened from the app's root menu — the pattern core blesses
three times over (res.config.settings, board.board, privacy_lookup's wizard):
a form with no res_id opens in new-record mode, default_get rebuilds the whole
state server-side on every visit, and object buttons auto-save the row so a
click can hand back a filtered window action. No custom JavaScript anywhere.

The counters are computed in default_get as the CURRENT user, so record rules
do the scoping they already do everywhere else — a lawyer's numbers are their
own and their team's, a supervisor's are the company's. Sections appear by
group membership; the group implication chain (manager ⊃ supervisor ⊃ lawyer,
manager ⊃ accountant) means a manager sees everything and that is correct.
"""
from datetime import timedelta

from odoo import _, api, fields, models


class LegalDashboard(models.TransientModel):
    _name = 'legal.dashboard'
    _description = 'Law Firm Dashboard'
    # The row exists only long enough to click a button (privacy_lookup idiom).
    _transient_max_hours = 24

    @api.depends_context('lang')
    def _compute_display_name(self):
        # Without this the breadcrumb and browser tab read "legal.dashboard,12".
        for record in self:
            record.display_name = record.env._('Dashboard')

    # role visibility — resolved server-side, not left to view groups alone,
    # so tests can assert who sees what
    show_lawyer = fields.Boolean(readonly=True)
    show_supervisor = fields.Boolean(readonly=True)
    show_manager = fields.Boolean(readonly=True)
    show_accountant = fields.Boolean(readonly=True)
    show_ai = fields.Boolean(readonly=True)

    # Whose work the top section counts. A supervisor runs a practice, so
    # theirs opens on the whole firm and narrows on request; a lawyer has no
    # choice to make, because the record rules only ever show them their own.
    scope = fields.Selection([
        ('firm', 'Whole Firm'),
        ('mine', 'Only Mine'),
    ], default='mine', required=True, string='Showing')

    # counters — computed, not defaulted, so flipping the scope reprices the
    # whole page without a reload
    my_open_cases = fields.Integer(compute='_compute_counters')
    my_hearings_week = fields.Integer(compute='_compute_counters')
    my_deadlines_overdue = fields.Integer(compute='_compute_counters')
    my_deadlines_week = fields.Integer(compute='_compute_counters')
    my_draft_cases = fields.Integer(compute='_compute_counters')
    my_draft_time = fields.Integer(compute='_compute_counters')

    # supervisor
    blocked_conflicts = fields.Integer(compute='_compute_counters')
    unassigned_cases = fields.Integer(compute='_compute_counters')

    # manager
    ai_needs_review = fields.Integer(compute='_compute_counters')

    # accountant
    billable_time = fields.Integer(compute='_compute_counters')
    approved_expenses = fields.Integer(compute='_compute_counters')
    draft_engagements = fields.Integer(compute='_compute_counters')

    # ------------------------------------------------------------- domains
    # One place per tile: the count in default_get and the button behind it
    # read the same domain, so the number and the list it opens cannot drift.
    def _mine(self, *conditions):
        """Narrow a domain to the current user unless the whole firm is asked for.

        Only a supervisor may widen: for anyone else the record rules answer
        the same question anyway, and an empty condition would just look like
        a broken filter.
        """
        if self.scope == 'firm' and self.env.user.has_group(
                'era_law_firm.group_legal_supervisor'):
            return []
        return list(conditions)

    def _domain_my_open_cases(self):
        return [('state', '=', 'confirmed')] + self._mine(
            '|', ('lawyer_id', '=', self.env.uid), ('team_user_ids', 'in', self.env.uid))

    def _domain_my_hearings_week(self):
        now = fields.Datetime.now()
        # The stock 'upcoming' filter has no upper bound; a week is what a
        # person plans around.
        return [('state', '!=', 'cancelled'),
                ('start_datetime', '>=', now),
                ('start_datetime', '<', now + timedelta(days=7))
                ] + self._mine(('lawyer_id', '=', self.env.uid))

    def _domain_my_deadlines_overdue(self):
        # Overdue is not a state: it is an open deadline whose date has passed.
        return [('state', 'in', ('draft', 'confirmed')),
                ('deadline_date', '<', fields.Date.today())
                ] + self._mine(('user_id', '=', self.env.uid))

    def _domain_my_deadlines_week(self):
        today = fields.Date.today()
        return [('state', 'in', ('draft', 'confirmed')),
                ('deadline_date', '>=', today),
                ('deadline_date', '<', today + timedelta(days=7))
                ] + self._mine(('user_id', '=', self.env.uid))

    def _domain_my_draft_cases(self):
        return [('state', '=', 'draft')] + self._mine(
            '|', ('lawyer_id', '=', self.env.uid), ('team_user_ids', 'in', self.env.uid))

    def _domain_my_draft_time(self):
        return [('state', '=', 'draft')] + self._mine(('user_id', '=', self.env.uid))

    def _domain_blocked_conflicts(self):
        return [('state', '=', 'blocked')]

    def _domain_unassigned_cases(self):
        return [('lawyer_id', '=', False),
                ('state', 'not in', ('closed', 'cancelled'))]

    def _domain_ai_needs_review(self):
        return ['|', ('cited_repealed', '=', True), ('cited_nothing', '=', True)]

    def _domain_billable_time(self):
        return [('state', '=', 'billable'), ('invoice_line_id', '=', False)]

    def _domain_approved_expenses(self):
        return [('state', '=', 'approved'), ('invoice_line_id', '=', False)]

    def _domain_draft_engagements(self):
        return [('state', '=', 'draft')]

    # ------------------------------------------------------- roles and counts
    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        has = self.env.user.has_group
        values.update({
            'show_lawyer': has('era_law_firm.group_legal_staff'),
            'show_supervisor': has('era_law_firm.group_legal_supervisor'),
            'show_manager': has('era_law_firm.group_legal_manager'),
            'show_accountant': has('era_law_firm.group_legal_accountant'),
            'show_ai': 'legal.ai.request' in self.env
                       and has('era_law_firm_ai.group_legal_ai_user'),
            # Whoever runs a practice opens on the practice.
            'scope': 'firm' if has('era_law_firm.group_legal_supervisor') else 'mine',
        })
        return values

    @api.depends('scope')
    def _compute_counters(self):
        """Every tile, repriced whenever the scope changes.

        Computed rather than defaulted so the switch is a round trip, not a
        reload: the client sends the new scope, the server counts again, and
        the number a tile shows is the number its button will list.
        """
        for record in self:
            has = record.env.user.has_group
            counters = {
                'my_open_cases': ('legal.case', record._domain_my_open_cases()),
                'my_hearings_week': ('legal.hearing', record._domain_my_hearings_week()),
                'my_deadlines_overdue': ('legal.deadline', record._domain_my_deadlines_overdue()),
                'my_deadlines_week': ('legal.deadline', record._domain_my_deadlines_week()),
                'my_draft_cases': ('legal.case', record._domain_my_draft_cases()),
                'my_draft_time': ('legal.time.entry', record._domain_my_draft_time()),
                'blocked_conflicts': ('legal.conflict.check', record._domain_blocked_conflicts()),
                'unassigned_cases': ('legal.case', record._domain_unassigned_cases()),
                'billable_time': ('legal.time.entry', record._domain_billable_time()),
                'approved_expenses': ('legal.expense', record._domain_approved_expenses()),
                'draft_engagements': ('legal.engagement', record._domain_draft_engagements()),
            }
            if 'legal.ai.request' in record.env and has('era_law_firm_ai.group_legal_ai_user'):
                counters['ai_needs_review'] = (
                    'legal.ai.request', record._domain_ai_needs_review())
            else:
                record.ai_needs_review = 0
            for name, (model, domain) in counters.items():
                record[name] = (record.env[model].search_count(domain)
                                if record.env[model].has_access('read') else 0)

    # -------------------------------------------------------------- actions
    def _open(self, xmlid, domain, name=None, context=None):
        """An existing action with the tile's own domain, list views included."""
        action = self.env['ir.actions.act_window']._for_xml_id(xmlid)
        action.update({'domain': domain, 'context': context or {}})
        if name:
            action['name'] = name
        return action

    def action_my_open_cases(self):
        return self._open('era_law_firm.action_legal_case',
                          self._domain_my_open_cases(), _('My Open Cases'))

    def action_my_hearings_week(self):
        return self._open('era_law_firm.action_legal_hearing',
                          self._domain_my_hearings_week(), _('Hearings — Next 7 Days'))

    def action_my_deadlines_overdue(self):
        return self._open('era_law_firm.action_legal_deadline',
                          self._domain_my_deadlines_overdue(), _('Overdue Deadlines'))

    def action_my_deadlines_week(self):
        return self._open('era_law_firm.action_legal_deadline',
                          self._domain_my_deadlines_week(), _('Deadlines — Next 7 Days'))

    def action_my_draft_cases(self):
        return self._open('era_law_firm.action_legal_case',
                          self._domain_my_draft_cases(), _('Cases Awaiting a Conflict Check'))

    def action_my_draft_time(self):
        return self._open('era_law_firm.action_legal_time_entry',
                          self._domain_my_draft_time(), _('My Unbilled Draft Time'))

    def action_blocked_conflicts(self):
        return self._open('era_law_firm.action_legal_conflict_check',
                          self._domain_blocked_conflicts(), _('Blocked Conflict Checks'))

    def action_unassigned_cases(self):
        return self._open('era_law_firm.action_legal_case',
                          self._domain_unassigned_cases(), _('Unassigned Cases'))

    def action_cases_by_lawyer(self):
        return self._open('era_law_firm.action_legal_case',
                          [('state', '=', 'confirmed')], _('Case Load by Lawyer'),
                          context={'group_by': 'lawyer_id'})

    def action_ai_needs_review(self):
        return self._open('era_law_firm_ai.action_ai_request',
                          self._domain_ai_needs_review(), _('AI Answers Needing Review'))

    def action_billable_time(self):
        return self._open('era_law_firm.action_legal_time_entry',
                          self._domain_billable_time(), _('Billable, Not Invoiced'))

    def action_approved_expenses(self):
        return self._open('era_law_firm.action_legal_expense',
                          self._domain_approved_expenses(), _('Approved, Not Invoiced'))

    def action_draft_engagements(self):
        return self._open('era_law_firm.action_legal_engagement',
                          self._domain_draft_engagements(), _('Engagements Awaiting Activation'))

    def action_trust_accounts(self):
        return self._open('era_law_firm.action_trust_account', [], _('Client Trust Accounts'))

    def action_open_intake(self):
        return self.env['ir.actions.act_window']._for_xml_id(
            'era_law_firm.action_legal_intake_wizard')

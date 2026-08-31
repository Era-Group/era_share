"""Tell the lawyer what the file is waiting for.

The case has gates — a conflict check before it can be confirmed, a settled
trust balance before it can close — and each announces itself only at the
moment someone tries and is refused. That is a usable system for whoever built
it and a guessing game for whoever inherits the file: the button is there, it
looks live, and pressing it produces a sentence explaining what should have
happened first.

So the same rules are read forwards as well. The banner says what the case
needs next and why it matters, before anyone runs into it.
"""
from odoo import _, api, fields, models


class LegalCase(models.Model):
    _inherit = 'legal.case'

    next_step = fields.Html(
        string='What this case needs next', compute='_compute_next_step',
        help="Read from the same rules that refuse the buttons, so the guidance "
             "and the gates can never disagree.")

    @api.depends('state', 'client_id', 'lawyer_id', 'case_type', 'stage_id',
                 'conflict_check_id', 'conflict_check_id.state',
                 'party_ids', 'engagement_ids', 'engagement_ids.state',
                 'trust_allocated_amount')
    def _compute_next_step(self):
        for case in self:
            case.next_step = case._render_next_step(case._next_step_items())

    def _next_step_items(self):
        """(title, why) for everything standing between here and the next state."""
        self.ensure_one()
        items = []
        if self.state == 'draft':
            missing = [
                label for value, label in (
                    (self.client_id, _('the client')),
                    (self.lawyer_id, _('the responsible lawyer')),
                    (self.case_type, _('the case type')),
                    (self.stage_id, _('the stage')),
                ) if not value]
            if missing:
                items.append((
                    _('Fill in %s', ', '.join(missing)),
                    _('A case cannot be confirmed while any of these is blank.')))
            check = self.conflict_check_id
            if not check:
                items.append((
                    _('Run a conflict check'),
                    _('It compares this file\'s parties against every confirmed and '
                      'closed case — by contact, by ID number, and by name — so an '
                      'engagement is not accepted against a former client.')))
            elif check.party_signature != self._party_signature():
                items.append((
                    _('Re-run the conflict check'),
                    _('The parties changed after the last check, so its result no '
                      'longer describes this file.')))
            elif check.state == 'blocked':
                items.append((
                    _('Resolve the conflict, or have a manager override it with a reason'),
                    _('The parties on this file appear on other cases. The reason is '
                      'kept with the file.')))
            elif check.state in ('clear', 'overridden'):
                items.append((
                    _('Confirm the case'),
                    _('Everything a confirmation needs is in place.')))
        elif self.state == 'confirmed':
            if not self.engagement_ids:
                items.append((
                    _('Open an engagement'),
                    _('Time and expenses attach to an engagement; without one there '
                      'is nothing to bill them against.')))
            elif not self.engagement_ids.filtered(lambda e: e.state == 'active'):
                items.append((
                    _('Activate the engagement'),
                    _('A draft engagement does not carry time or expenses.')))
            if self.trust_allocated_amount:
                items.append((
                    _('Client money is held for this case: %s', self.trust_allocated_amount),
                    _('It has to be applied to an invoice or refunded before the case '
                      'can close.')))
        elif self.state == 'closed':
            items.append((_('This case is closed.'), _('Nothing further is required.')))
        return items

    @api.model
    def _render_next_step(self, items):
        if not items:
            return ''
        rows = ''.join(
            '<li><strong>%s</strong><br/><span class="text-muted">%s</span></li>'
            % (title, why) for title, why in items)
        return '<ul class="mb-0">%s</ul>' % rows

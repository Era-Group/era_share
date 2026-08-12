# -*- coding: utf-8 -*-
"""The opportunity link, plugged into the seams ``era_sembly_meetings`` defines.

Nothing here reaches into the base app's internals: it adds one field and
implements five seam methods. That is the whole contract, and it is why this
module can be installed, uninstalled or replaced without the base app or the
sibling link modules noticing.
"""
from datetime import datetime, timedelta

from odoo import api, fields, models

# Hard cap on what reaches the LLM. With 2 000+ leads on a live database the
# whole set can never be handed to a model.
MAX_LEAD_CANDIDATES = 60
# A won opportunity is still a plausible subject if it was touched recently.
RECENT_DAYS = 180
# Prompt block order: opportunities first, then projects, tasks, tickets.
POOL_SEQUENCE = 10


class SemblyMeeting(models.Model):
    _inherit = 'sembly.meeting'

    lead_id = fields.Many2one(
        'crm.lead', string="الفرصة", ondelete='set null', index=True, tracking=True)

    # ------------------------------------------------------------------ seams
    @api.model
    def _sembly_link_field_by_model(self):
        """SEAM — lets the base file an internal meeting here, and puts this
        model in the settings picker, without the base naming it."""
        return dict(super()._sembly_link_field_by_model(), **{'crm.lead': 'lead_id'})

    @api.model
    def _sembly_link_fields(self):
        """A hand-picked opportunity claims the record: the matcher must then
        leave it alone (requirement 3)."""
        return super()._sembly_link_fields() | {'lead_id'}

    def _ai_candidate_pools(self):
        """Contribute the opportunity pool, narrowed DETERMINISTICALLY first.

        Two signals, unioned then capped: the participants' own opportunities,
        and opportunities whose name or customer matches a significant word of
        the meeting title. Whatever the cap dropped is reported through
        ``basis`` so a truncated candidate set is never invisible.
        """
        self.ensure_one()
        pools = super()._ai_candidate_pools()
        Lead = self.env['crm.lead'].sudo()

        leads = Lead.browse()
        basis = []

        partners = self._candidate_partners()
        if partners:
            cutoff = fields.Datetime.to_string(
                datetime.utcnow() - timedelta(days=RECENT_DAYS))
            leads |= Lead.search([
                ('partner_id', 'in', partners.ids),
                ('active', '=', True),
                '|', ('stage_id.is_won', '=', False), ('write_date', '>=', cutoff),
            ], limit=MAX_LEAD_CANDIDATES)
            basis.append('participants(%d)' % len(partners))

        tokens = self._title_tokens()[:6]
        if tokens:
            for token in tokens:
                leads |= Lead.search([
                    ('active', '=', True),
                    '|', ('name', 'ilike', token), ('partner_name', 'ilike', token),
                ], limit=15)
            basis.append('title-tokens(%s)' % ",".join(tokens))

        # An old meeting usually belongs to an opportunity that has since been
        # archived, so archived leads are candidates too — but only AFTER the
        # active ones. A live database carries far more archived leads than
        # live ones, and searching both together would let them crowd the
        # active ones out of the cap.
        room = MAX_LEAD_CANDIDATES - len(leads)
        if room > 0:
            archived = Lead.with_context(active_test=False).browse()
            if partners:
                archived |= Lead.with_context(active_test=False).search(
                    [('partner_id', 'in', partners.ids), ('active', '=', False)],
                    order='write_date desc', limit=room)
            for token in tokens:
                if len(archived) >= room:
                    break
                archived |= Lead.with_context(active_test=False).search(
                    ['&', ('active', '=', False),
                     '|', ('name', 'ilike', token), ('partner_name', 'ilike', token)],
                    order='write_date desc', limit=10)
            archived = (archived - leads)[:room]
            if archived:
                leads |= archived
                basis.append('archived(%d)' % len(archived))

        if len(leads) > MAX_LEAD_CANDIDATES:
            leads = leads[:MAX_LEAD_CANDIDATES]
            basis.append('TRUNCATED>%d' % MAX_LEAD_CANDIDATES)

        pools.append({
            'key': 'lead_id',
            'label': "CANDIDATE OPPORTUNITIES (id | name | customer | stage)",
            'records': leads,
            # ARCHIVED is stated, not hidden: "this opportunity is closed and
            # filed away" is exactly the context that tells an old meeting
            # apart from a live one.
            'render': lambda rec: '%s | %s | customer: %s | stage: %s%s' % (
                rec.id, rec.name,
                rec.partner_id.display_name or rec.partner_name or '-',
                rec.stage_id.display_name or '-',
                '' if rec.active else ' | ARCHIVED'),
            'sequence': POOL_SEQUENCE,
            'basis': 'leads(%s)' % (",".join(basis) or 'none'),
        })
        return pools

    def _has_external_link(self):
        """An opportunity is a more specific answer than a project, so it stops
        ``era_sembly_meetings_tasks`` filing the meeting under a bucket task."""
        self.ensure_one()
        return bool(self.lead_id) or super()._has_external_link()

    def _summary_targets(self):
        """Requirement 6: the opportunity receives the internal note."""
        targets = super()._summary_targets()
        if self.lead_id:
            targets.append(self.lead_id)
        return targets

    # ------------------------------------------------------------------ actions
    def action_open_linked_lead(self):
        return self._action_open_link('lead_id')

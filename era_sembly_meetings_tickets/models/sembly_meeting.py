# -*- coding: utf-8 -*-
"""The helpdesk ticket link, plugged into the seams the base app defines."""
from odoo import api, fields, models

# Hard cap on what reaches the LLM.
MAX_TICKET_CANDIDATES = 40
# Prompt block order: last, after opportunities, projects and tasks.
POOL_SEQUENCE = 40


class SemblyMeeting(models.Model):
    _inherit = 'sembly.meeting'

    ticket_id = fields.Many2one(
        'helpdesk.ticket', string="التذكرة", ondelete='set null',
        index=True, tracking=True)

    # ------------------------------------------------------------------ seams
    @api.model
    def _sembly_link_field_by_model(self):
        """SEAM — lets the base file an internal meeting here, and puts this
        model in the settings picker, without the base naming it."""
        return dict(super()._sembly_link_field_by_model(), **{'helpdesk.ticket': 'ticket_id'})

    @api.model
    def _sembly_link_fields(self):
        return super()._sembly_link_fields() | {'ticket_id'}

    def _ai_candidate_pools(self):
        """Contribute the ticket pool, narrowed the same way the others are."""
        self.ensure_one()
        pools = super()._ai_candidate_pools()
        Ticket = self.env['helpdesk.ticket'].sudo()

        tickets = Ticket.browse()
        basis = []

        partners = self._candidate_partners()
        if partners:
            tickets |= Ticket.search(
                [('partner_id', 'in', partners.ids)],
                order='write_date desc', limit=MAX_TICKET_CANDIDATES)
            basis.append('participants(%d)' % len(partners))

        tokens = self._title_tokens()[:6]
        if tokens:
            for token in tokens:
                tickets |= Ticket.search(
                    [('name', 'ilike', token)], order='write_date desc', limit=15)
            basis.append('title-tokens(%s)' % ",".join(tokens))

        # The ambient set: a folded stage means the ticket is closed.
        tickets |= Ticket.search(
            [('stage_id.fold', '=', False)], order='write_date desc', limit=40)
        basis.append('open-tickets')

        if len(tickets) > MAX_TICKET_CANDIDATES:
            tickets = tickets[:MAX_TICKET_CANDIDATES]
            basis.append('TRUNCATED>%d' % MAX_TICKET_CANDIDATES)

        pools.append({
            'key': 'ticket_id',
            'label': "CANDIDATE TICKETS (id | name | customer | stage)",
            'records': tickets,
            'render': lambda rec: '%s | %s | customer: %s | stage: %s' % (
                rec.id, rec.name, rec.partner_id.display_name or '-',
                rec.stage_id.display_name or '-'),
            'sequence': POOL_SEQUENCE,
            'basis': 'tickets(%s)' % (",".join(basis) or 'none'),
        })
        return pools

    def _has_external_link(self):
        """A linked ticket counts as a match, so the project bucket-task
        fallback does not fire on top of it."""
        self.ensure_one()
        return bool(self.ticket_id) or super()._has_external_link()

    def _summary_targets(self):
        """Requirement 6: the ticket receives the internal note too."""
        targets = super()._summary_targets()
        if self.ticket_id:
            targets.append(self.ticket_id)
        return targets

    # ------------------------------------------------------------------ actions
    def action_open_linked_ticket(self):
        return self._action_open_link('ticket_id')

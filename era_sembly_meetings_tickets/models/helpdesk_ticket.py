# -*- coding: utf-8 -*-
"""Requirement 5 — the meetings smart button on a helpdesk ticket."""
from odoo import _, fields, models


class HelpdeskTicket(models.Model):
    _inherit = 'helpdesk.ticket'

    sembly_meeting_ids = fields.One2many(
        'sembly.meeting', 'ticket_id', string="اجتماعات Sembly")
    sembly_meeting_count = fields.Integer(
        string="عدد الاجتماعات", compute='_compute_sembly_meeting_count')

    def _compute_sembly_meeting_count(self):
        counts = {}
        if self.ids:
            groups = self.env['sembly.meeting'].sudo()._read_group(
                [('ticket_id', 'in', self.ids)], ['ticket_id'], ['__count'])
            counts = {ticket.id: count for ticket, count in groups}
        for ticket in self:
            ticket.sembly_meeting_count = counts.get(ticket.id, 0)

    def action_view_sembly_meetings(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _("الاجتماعات"),
            'res_model': 'sembly.meeting',
            'view_mode': 'list,kanban,form',
            'domain': [('ticket_id', '=', self.id)],
            'context': {'default_ticket_id': self.id},
        }

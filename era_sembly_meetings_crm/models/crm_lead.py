# -*- coding: utf-8 -*-
"""Requirement 5 — the meetings smart button on an opportunity."""
from odoo import _, fields, models


class CrmLead(models.Model):
    _inherit = 'crm.lead'

    sembly_meeting_ids = fields.One2many(
        'sembly.meeting', 'lead_id', string="اجتماعات Sembly")
    sembly_meeting_count = fields.Integer(
        string="عدد الاجتماعات", compute='_compute_sembly_meeting_count')

    def _compute_sembly_meeting_count(self):
        counts = {}
        if self.ids:
            groups = self.env['sembly.meeting'].sudo()._read_group(
                [('lead_id', 'in', self.ids)], ['lead_id'], ['__count'])
            counts = {lead.id: count for lead, count in groups}
        for lead in self:
            lead.sembly_meeting_count = counts.get(lead.id, 0)

    def action_view_sembly_meetings(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _("الاجتماعات"),
            'res_model': 'sembly.meeting',
            'view_mode': 'list,kanban,form',
            'domain': [('lead_id', '=', self.id)],
            'context': {'default_lead_id': self.id},
        }

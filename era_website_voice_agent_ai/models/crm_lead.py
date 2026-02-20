# -*- coding: utf-8 -*-
from odoo import fields, models


class CrmLead(models.Model):
    _inherit = "crm.lead"

    realtime_call_summary_count = fields.Integer(
        string="المكالمات",
        compute="_compute_realtime_call_summary_count",
    )

    def _compute_realtime_call_summary_count(self):
        Summary = self.env["crm.realtime_call_summary"]
        for lead in self:
            lead.realtime_call_summary_count = Summary.search_count([("lead_id", "=", lead.id)])

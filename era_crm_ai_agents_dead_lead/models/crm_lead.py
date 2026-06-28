# -*- coding: utf-8 -*-
"""Dead-Lead send bookkeeping on crm.lead.

A single stamp recording when the Dead-Lead agent last sent a comeback for this
lead. It is set by the send step (task 3.6) and read by the scan (task 3.7) as a
cooldown signal so the same lead is not re-contacted on every run.
"""
from odoo import fields, models


class CrmLead(models.Model):
    _inherit = "crm.lead"

    crm_ai_dead_lead_last_sent = fields.Datetime(
        string="Dead-Lead: Last Comeback Sent",
        readonly=True, copy=False,
        help="When the Dead-Lead Resurrection agent last sent an approved "
             "comeback message for this lead (set on send). Used by the scan as "
             "a cooldown signal.")

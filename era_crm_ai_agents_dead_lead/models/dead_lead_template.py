# -*- coding: utf-8 -*-
"""The approved comeback-message template (manager-editable).

The Dead-Lead agent never free-writes a message: it fills ONLY the personal
parts of a manager-approved template (see the 3.00 overview and the No-Hardcoded
-Policy rule — an editable body is policy, so it lives in a small manager-
editable model, not in code). ``template_id`` on ``crm.ai.dead.lead.agent``
points here. The drafting engine (task 3.4) consumes ``body``; the human still
approves the final text before it is sent (task 3.5).
"""
from odoo import fields, models


class CrmAiDeadLeadTemplate(models.Model):
    _name = "crm.ai.dead.lead.template"
    _description = "Dead-Lead Comeback Template"
    _order = "sequence, id"

    name = fields.Char(
        required=True, translate=True,
        help="Internal label for this approved comeback template.")
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    body = fields.Text(
        required=True, translate=True,
        help="The approved comeback message body (Arabic). The agent fills only "
             "the personal parts; a human approves the final text before sending.")
    note = fields.Text(
        help="Optional guidance for the salesperson / reviewer about when this "
             "template is appropriate.")

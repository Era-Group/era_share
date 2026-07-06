# -*- coding: utf-8 -*-
from odoo import fields, models


class CrmAiCampaignCategory(models.Model):
    """Simple manager-editable classification for service-catalog entries."""

    _name = "crm.ai.campaign.category"
    _description = "CRM AI Campaign Service Category"
    _order = "name"

    name = fields.Char(required=True, translate=True)
    active = fields.Boolean(default=True)

    # Odoo 19: _sql_constraints is no longer supported; use models.Constraint.
    _name_uniq = models.Constraint(
        "unique(name)",
        "A campaign service category with this name already exists.")

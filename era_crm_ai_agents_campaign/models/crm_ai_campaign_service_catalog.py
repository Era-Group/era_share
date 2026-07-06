# -*- coding: utf-8 -*-
from odoo import fields, models


class CrmAiCampaignServiceCatalog(models.Model):
    """The clean, manager-curated list of things the agent may promote.

    This catalog is the GROUNDING for the LLM: the matching prompt presents
    only the ACTIVE records here, and any service the model returns that is not
    in this active set is rejected (the model must never invent a service).
    ``description`` is what the LLM reads to understand the offering;
    ``target_tag_ids`` narrows which partner segments a service is pitched to.
    """

    _name = "crm.ai.campaign.service.catalog"
    _description = "CRM AI Campaign Service Catalog"
    _order = "name"

    name = fields.Char(required=True, translate=True)
    service_type = fields.Selection(
        selection=[
            ("core_product", "Core Product"),
            ("service", "Service"),
            ("featured_module", "Featured Module"),
        ],
        string="Type",
        required=True,
        default="service",
    )
    category_id = fields.Many2one(
        comodel_name="crm.ai.campaign.category",
        string="Category",
        ondelete="set null",
    )
    description = fields.Text(
        help="What this offering is and who it helps. This text grounds the "
             "LLM's service matching — write it for the model as much as for "
             "a colleague.",
    )
    target_tag_ids = fields.Many2many(
        comodel_name="res.partner.category",
        relation="crm_ai_campaign_service_partner_tag_rel",
        string="Target Segments (partner tags)",
        help="Partner tags this service is aimed at. Empty = suitable for any "
             "segment. Used to pre-filter which services are offered to the "
             "LLM for a given customer.",
    )
    active = fields.Boolean(default=True)

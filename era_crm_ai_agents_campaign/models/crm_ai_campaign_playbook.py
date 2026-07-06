# -*- coding: utf-8 -*-
from odoo import fields, models


class CrmAiCampaignPlaybook(models.Model):
    """Special-case messaging routes, matched to partners by tag.

    Example: partners tagged ``new_customer`` (no Odoo background) get a
    playbook whose instruction tells the LLM to open with a short introduction
    to what Odoo is before presenting the matched service. When the engine
    builds a partner's message it collects the matching playbooks (by tag,
    ordered by ``sequence``) and injects their instructions into the prompt.
    """

    _name = "crm.ai.campaign.playbook"
    _description = "CRM AI Campaign Playbook"
    _order = "sequence, id"

    name = fields.Char(required=True, translate=True)
    trigger_tag_ids = fields.Many2many(
        comodel_name="res.partner.category",
        relation="crm_ai_campaign_playbook_partner_tag_rel",
        string="Trigger Tags",
        help="Partners carrying ANY of these tags get this playbook's "
             "instruction injected into their drafting prompt. A playbook with "
             "no trigger tags never fires.",
    )
    sequence = fields.Integer(
        default=10,
        help="Evaluation order: lower sequences are injected first.",
    )
    instruction = fields.Text(
        required=True,
        help="Extra guidance injected into the LLM prompt for matching "
             "partners, e.g. 'Recipient is new to Odoo; open with a short "
             "intro to what Odoo is before presenting the service.'",
    )
    active = fields.Boolean(default=True)

# -*- coding: utf-8 -*-
from odoo import api, fields, models


class CrmAiModel(models.Model):
    """Pricing rate card for AI models — NOT a model catalog.

    The list of *available* models is owned by Odoo native AI (we don't define or
    maintain it). This table adds the one thing native AI does not expose:
    PER-TOKEN PRICING, so the hard cost cap (Rule 14) can compute spend. It is a
    rate card keyed by the native model ``code``.

    Model SELECTION lives on ``crm.ai.agent`` (``model_code`` / ``model_code_advanced``),
    not here. Keys come only from the server environment via native AI; nothing
    secret is stored here.

    Keep this card complete as providers change prices or Odoo adds models: a call
    on a code with no active row here is a Rule-14 fail-safe (blocked by default,
    or flagged ``unpriced`` on the usage row) — see the AI Compliance Guard.
    """

    _name = "crm.ai.model"
    _description = "CRM AI Model Pricing (Rate Card)"
    _order = "provider, name"

    name = fields.Char(required=True, translate=True)
    code = fields.Char(
        string="Model Code",
        required=True,
        help="The native model identifier this price applies to "
             "(e.g. 'gpt-4o', 'gemini-2.5-flash'). Must match the code an agent "
             "uses and a model Odoo native AI supports.",
    )
    provider = fields.Selection(
        selection=[("openai", "OpenAI"), ("google", "Google")],
        string="Provider",
        help="Informational label (OpenAI / Google). Selection is driven by the "
             "agent's model code, not by this field.",
    )
    price_input_1k = fields.Float(
        string="Input Price / 1K",
        digits=(12, 6),
        help="Cost in USD per 1,000 input (prompt) tokens.",
    )
    price_output_1k = fields.Float(
        string="Output Price / 1K",
        digits=(12, 6),
        help="Cost in USD per 1,000 output (completion) tokens.",
    )
    active = fields.Boolean(default=True)

    _sql_constraints = [
        ("code_uniq", "unique(code)", "The model code must be unique."),
    ]

    @api.depends("name", "provider")
    def _compute_display_name(self):
        for record in self:
            if record.provider:
                provider_label = dict(
                    self._fields["provider"]._description_selection(self.env)
                ).get(record.provider, record.provider)
                record.display_name = f"{record.name} ({provider_label})"
            else:
                record.display_name = record.name or ""

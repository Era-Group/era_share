from odoo import api, fields, models


class EraAiModel(models.Model):
    _name = "era.ai.model"
    _description = "AI Account Model"
    _order = "kind, label, model_id"

    account_id = fields.Many2one(
        "era.ai.account", required=True, ondelete="cascade", index=True,
    )
    provider = fields.Selection(related="account_id.provider", store=True)
    model_id = fields.Char(required=True, help="Provider model id, e.g. claude-opus-4-8 or gpt-4o.")
    label = fields.Char()
    kind = fields.Selection(
        selection=[("chat", "Chat"), ("embedding", "Embedding"), ("image", "Image")],
        default="chat", required=True,
    )
    cost_info = fields.Char(
        string="Rate",
        help="Approximate provider rate for this model. NOTE: Cloudflare bills in "
             "'Neurons' — a compute-usage unit, not money — with 10,000 free per day; "
             "beyond that they cost USD $0.011 per 1,000 Neurons. Indicative, captured "
             "at sync time — always confirm on the provider's live pricing page.",
    )
    active = fields.Boolean(default=True)

    _sql_constraints = [
        ("uniq_model_per_account",
         "unique(account_id, model_id, kind)",
         "This model already exists for the account."),
    ]

    @api.depends("label", "model_id")
    def _compute_display_name(self):
        for rec in self:
            rec.display_name = rec.label or rec.model_id or ""

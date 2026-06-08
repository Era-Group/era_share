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
        selection=[("chat", "Chat"), ("embedding", "Embedding")],
        default="chat", required=True,
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

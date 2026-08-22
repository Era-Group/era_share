from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class EraAiModel(models.Model):
    _name = "era.ai.model"
    _description = "AI Account Model"
    _order = "kind, label, model_id"
    # display_name is computed (label or model_id); without this, a name search
    # on the relation logs "Cannot search on display_name, no _rec_name…".
    _rec_names_search = ["label", "model_id"]

    account_id = fields.Many2one(
        "era.ai.account", required=True, ondelete="cascade", index=True,
    )
    provider = fields.Selection(related="account_id.provider", store=True)
    model_id = fields.Char(required=True, help="Provider model id, e.g. claude-opus-4-8 or gpt-4o.")
    label = fields.Char()
    kind = fields.Selection(
        selection=[
            ("chat", "Chat"),
            ("embedding", "Embedding"),
            ("image", "Image"),
            ("transcription", "Transcription"),
        ],
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

    # New-style table constraint (Odoo 19); resolves to the same SQL constraint
    # name as the legacy _sql_constraints entry, so upgrades are a no-op.
    _uniq_model_per_account = models.Constraint(
        "unique(account_id, model_id, kind)",
        "This model already exists for the account.",
    )

    @api.constrains("kind", "account_id")
    def _check_kind_for_cli_proxy(self):
        # The CLI proxies (claude / codex) are text-only. An image/embedding row
        # on such an account is a trap: it shows up in other modules' model
        # pickers (e.g. era_seo_suite's cover-image picker) but every call is
        # refused at runtime — block it at the source instead.
        for rec in self:
            if rec.kind != "chat" and rec.account_id.auth_mode == "cli_proxy":
                raise ValidationError(_(
                    "The local CLI proxy is text-only — %(kind)s models cannot be "
                    "added to CLI-proxy account '%(account)s'. For images, use an "
                    "OpenAI account with an API key or Cloudflare Workers AI.",
                    kind=rec.kind, account=rec.account_id.name))

    @api.depends("label", "model_id")
    def _compute_display_name(self):
        for rec in self:
            rec.display_name = rec.label or rec.model_id or ""

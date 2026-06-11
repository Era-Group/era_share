from odoo import _, api, fields, models
from odoo.exceptions import UserError


class EraAiAccountLogin(models.TransientModel):
    """Link a subscription account to an era.ai.account, provider-specific:

    * Anthropic — two-step 'Login with Claude'. The server has no browser, so we
      use Claude Code's manual copy-code redirect: the manager opens the
      authorize URL in their own browser, approves access, and pastes back the
      ``code#state`` Claude shows. We exchange it and store the token once, for
      the whole system (see era.ai.account._oauth_*).
    * OpenAI — paste ``auth.json``. OpenAI's OAuth client only redirects to
      localhost:1455 (no hosted copy-code page), so the manager runs
      ``codex login`` on their own machine and pastes the resulting
      ``~/.codex/auth.json`` here (OpenAI's documented server/CI pattern; see
      era.ai.account._codex_link_with_auth_json).
    """
    _name = "era.ai.account.login"
    _description = "Link a subscription AI account (Claude / ChatGPT)"

    account_id = fields.Many2one(
        "era.ai.account", required=True, ondelete="cascade")
    provider = fields.Selection(related="account_id.provider")
    authorize_url = fields.Char(readonly=True)
    code = fields.Char(string="Authorization code")
    auth_json = fields.Text(string="auth.json contents")
    linked = fields.Boolean(related="account_id.cli_oauth_linked")

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        account_id = res.get("account_id") or self.env.context.get("default_account_id")
        if account_id:
            account = self.env["era.ai.account"].browse(account_id).exists()
            if not account:
                raise UserError(_("AI account not found — reopen the dialog."))
            res["account_id"] = account.id
            # Claude only: mint a fresh PKCE pair + authorize URL as the dialog
            # opens. The Codex flow has no server-side OAuth state.
            if account.provider == "anthropic":
                res["authorize_url"] = account._oauth_start()
        return res

    def action_open_url(self):
        self.ensure_one()
        if not self.authorize_url:
            raise UserError(_("No authorization URL — reopen the dialog."))
        return {"type": "ir.actions.act_url", "url": self.authorize_url, "target": "new"}

    def action_complete(self):
        self.ensure_one()
        if self.account_id.provider == "openai":
            self.account_id._codex_link_with_auth_json(self.auth_json)
        else:
            self.account_id._oauth_complete(self.code)
        # Reload so the account form behind the dialog re-reads the (now linked)
        # on-disk credential state and shows the "linked" banner immediately.
        return {
            "type": "ir.actions.client",
            "tag": "soft_reload",
        }

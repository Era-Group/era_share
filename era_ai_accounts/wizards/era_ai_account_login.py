from odoo import _, api, fields, models
from odoo.exceptions import UserError


class EraAiAccountLogin(models.TransientModel):
    """Two-step 'Login with Claude' helper for an era.ai.account.

    The server has no browser, so we use Claude Code's manual copy-code redirect:
    the manager opens the authorize URL in their own browser, approves access, and
    pastes back the ``code#state`` Claude shows. We then exchange it and store the
    token once, for the whole system (see era.ai.account._oauth_*).
    """
    _name = "era.ai.account.login"
    _description = "Login with Claude (link a subscription account)"

    account_id = fields.Many2one(
        "era.ai.account", required=True, ondelete="cascade")
    authorize_url = fields.Char(readonly=True)
    code = fields.Char(string="Authorization code")
    linked = fields.Boolean(related="account_id.cli_oauth_linked")

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        account_id = res.get("account_id") or self.env.context.get("default_account_id")
        if account_id:
            account = self.env["era.ai.account"].browse(account_id).exists()
            if not account:
                raise UserError(_("AI account not found — reopen the dialog."))
            # Mint a fresh PKCE pair + authorize URL as the dialog opens.
            res["account_id"] = account.id
            res["authorize_url"] = account._oauth_start()
        return res

    def action_open_url(self):
        self.ensure_one()
        if not self.authorize_url:
            raise UserError(_("No authorization URL — reopen the dialog."))
        return {"type": "ir.actions.act_url", "url": self.authorize_url, "target": "new"}

    def action_complete(self):
        self.ensure_one()
        self.account_id._oauth_complete(self.code)
        # Reload so the account form behind the dialog re-reads the (now linked)
        # on-disk credential state and shows the "linked" banner immediately.
        return {
            "type": "ir.actions.client",
            "tag": "soft_reload",
        }

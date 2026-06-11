from odoo import _, api, fields, models
from odoo.exceptions import UserError


class EraAiAccountLogin(models.TransientModel):
    """Link a subscription account to an era.ai.account, provider-specific:

    * Anthropic — two-step 'Login with Claude'. The server has no browser, so we
      use Claude Code's manual copy-code redirect: the manager opens the
      authorize URL in their own browser, approves access, and pastes back the
      ``code#state`` Claude shows. We exchange it and store the token once, for
      the whole system (see era.ai.account._oauth_*).
    * OpenAI — two ways, simplest first:
      - **Device login**: the wizard runs ``codex login --device-auth`` on the
        server; the manager opens the shown link on any device, types the
        one-time code, approves — codex writes auth.json itself.
      - **Paste auth.json**: fallback when device codes are disabled — run
        ``codex login`` on your own machine and paste ``~/.codex/auth.json``
        (OpenAI's documented server/CI pattern; see
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

    # Device-code login (OpenAI): filled once the codex CLI prints them.
    device_url = fields.Char(readonly=True)
    device_code = fields.Char(readonly=True)
    device_status = fields.Char(readonly=True)

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
            # opens. The Codex flows have no server-side OAuth state.
            if account.provider == "anthropic":
                res["authorize_url"] = account._oauth_start()
        return res

    def _reopen(self):
        """Keep this same wizard dialog open after a button updated its fields."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
            "name": _("Connect ChatGPT account") if self.provider == "openai"
                    else _("Login with Claude"),
        }

    def action_open_url(self):
        self.ensure_one()
        if not self.authorize_url:
            raise UserError(_("No authorization URL — reopen the dialog."))
        return {"type": "ir.actions.act_url", "url": self.authorize_url, "target": "new"}

    # ----------------------------------------------------- device login (codex)
    def action_device_start(self):
        self.ensure_one()
        info = self.account_id._codex_device_login_start()
        self.write({
            "device_url": info["url"],
            "device_code": info["code"],
            "device_status": _(
                "Waiting for approval — open the link, enter the code, then "
                "click 'Check status'. The code expires in about 15 minutes."),
        })
        return self._reopen()

    def action_device_open_url(self):
        self.ensure_one()
        if not self.device_url:
            raise UserError(_("Start the device login first."))
        return {"type": "ir.actions.act_url", "url": self.device_url, "target": "new"}

    def action_device_check(self):
        self.ensure_one()
        status = self.account_id._codex_device_login_status()
        if status == "linked":
            # Reload so the account form behind the dialog shows the banner.
            return {"type": "ir.actions.client", "tag": "soft_reload"}
        if status == "pending":
            self.device_status = _(
                "Not approved yet — finish on your other device, then click "
                "'Check status' again.")
        else:  # failed:<detail>
            detail = status.split(":", 1)[1] if ":" in status else status
            self.write({
                "device_url": False, "device_code": False,
                "device_status": _("Device login did not complete (%s). Start "
                                   "again, or paste auth.json below.", detail),
            })
        return self._reopen()

    def action_complete(self):
        self.ensure_one()
        if self.account_id.provider == "openai":
            self.account_id._codex_link_with_auth_json(self.auth_json)
        else:
            self.account_id._oauth_complete(self.code)
        # The pasted auth.json carries long-lived OAuth tokens and transient
        # rows live in a real DB table until the vacuum — wipe it (and the
        # one-time code) now that the credentials are on disk.
        self.sudo().write({"auth_json": False, "code": False})
        # Reload so the account form behind the dialog re-reads the (now linked)
        # on-disk credential state and shows the "linked" banner immediately.
        return {
            "type": "ir.actions.client",
            "tag": "soft_reload",
        }

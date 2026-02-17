from datetime import timedelta

from odoo import api, fields, models


class VoipOneTabSession(models.Model):
    _name = "voip.one.tab.session"
    _description = "VoIP One Tab Session Lock"
    _rec_name = "user_id"
    _order = "last_seen desc"

    user_id = fields.Many2one("res.users", required=True, ondelete="cascade", index=True)
    tab_id = fields.Char(required=True, index=True)
    last_seen = fields.Datetime(required=True)

    def init(self):
        self.env.cr.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS voip_one_tab_session_user_unique
            ON voip_one_tab_session (user_id)
            """
        )

    @api.model
    def _lock_timeout(self):
        param = self.env["ir.config_parameter"].sudo().get_param(
            "voip_one_tab_lock_timeout", default="45"
        )
        try:
            timeout = int(param)
        except (TypeError, ValueError):
            timeout = 45
        return max(10, timeout)

    @api.model
    def _is_expired(self, last_seen):
        if not last_seen:
            return True
        return last_seen < fields.Datetime.now() - timedelta(seconds=self._lock_timeout())

    @api.model
    def acquire(self, tab_id, force=False):
        user = self.env.user
        session = self.search([("user_id", "=", user.id)], limit=1)
        now = fields.Datetime.now()
        forced = False
        if session and not self._is_expired(session.last_seen):
            if session.tab_id != tab_id:
                if not force:
                    return {"status": "denied"}
                forced = True
        vals = {"user_id": user.id, "tab_id": tab_id, "last_seen": now}
        if session:
            session.write(vals)
        else:
            self.create(vals)
        return {"status": "forced" if forced else "granted"}

    @api.model
    def heartbeat(self, tab_id):
        user = self.env.user
        session = self.search(
            [("user_id", "=", user.id), ("tab_id", "=", tab_id)], limit=1
        )
        if not session:
            return {"status": "denied"}
        session.write({"last_seen": fields.Datetime.now()})
        return {"status": "ok"}

    @api.model
    def release(self, tab_id):
        user = self.env.user
        session = self.search(
            [("user_id", "=", user.id), ("tab_id", "=", tab_id)], limit=1
        )
        if session:
            session.unlink()
        return {"status": "released"}

import logging

from odoo import api, fields, models, SUPERUSER_ID, _

_logger = logging.getLogger(__name__)

# Responses can carry up to 100 transactions; keep logs bounded.
MAX_LOG_CHARS = 20000


class BwatechApiLog(models.Model):
    _name = "bwatech.api.log"
    _description = "BWATECH API Log"
    _order = "id desc"

    connection_id = fields.Many2one(
        "bwatech.connection", ondelete="cascade", index=True, readonly=True
    )
    service = fields.Char(readonly=True, index=True, help="BWATECH service name, e.g. AccountList.")
    endpoint = fields.Char(readonly=True)

    message_id = fields.Char(
        readonly=True, index=True, help="messageID sent to BWATECH in the request header."
    )
    correlation_id = fields.Char(
        readonly=True,
        index=True,
        help="correlationID returned by BWATECH. Quote this when raising a ticket with BWATECH.",
    )

    state = fields.Selection(
        [("success", "Success"), ("error", "Error")], readonly=True, index=True
    )
    error_type = fields.Selection(
        [
            ("timeout", "Timeout"),
            ("ssl", "TLS / Certificate"),
            ("connection", "Connection"),
            ("http", "HTTP Error"),
            ("payload", "Invalid Response"),
            ("business", "BWATECH Business Error"),
            ("auth", "Authentication"),
        ],
        readonly=True,
    )
    error_message = fields.Text(readonly=True)

    http_status = fields.Integer(readonly=True)
    status_code = fields.Char(readonly=True, help="BWATECH responseHeader.status.statusCode.")
    status_description = fields.Char(readonly=True)

    request_body = fields.Text(readonly=True)
    response_body = fields.Text(readonly=True)
    duration_ms = fields.Integer(readonly=True, string="Duration (ms)")

    @api.model
    def _truncate(self, text):
        if text is None:
            return False
        text = str(text)
        if len(text) <= MAX_LOG_CHARS:
            return text
        return text[:MAX_LOG_CHARS] + "\n... [truncated %d chars]" % (len(text) - MAX_LOG_CHARS)

    @api.model
    def _record(self, vals, isolated=False):
        """Store one API call.

        :param isolated: write on a separate cursor so the log survives the
            rollback caused by the UserError the caller is about to raise.
            In test mode ``registry.cursor()`` is patched to the test cursor,
            so this stays transactional under tests.
        """
        vals = dict(vals)
        vals["request_body"] = self._truncate(vals.get("request_body"))
        vals["response_body"] = self._truncate(vals.get("response_body"))
        vals["error_message"] = self._truncate(vals.get("error_message"))
        try:
            if isolated:
                with self.env.registry.cursor() as cr:
                    api.Environment(cr, SUPERUSER_ID, {})["bwatech.api.log"].create(vals)
            else:
                self.sudo().create(vals)
        except Exception:
            # Logging must never mask the real API error.
            _logger.exception("Failed to store BWATECH API log for service %s", vals.get("service"))

    @api.model
    def _gc(self, retention_days):
        """Delete logs older than ``retention_days``."""
        if not retention_days or retention_days <= 0:
            return 0
        limit_date = fields.Datetime.subtract(fields.Datetime.now(), days=retention_days)
        stale = self.sudo().search([("create_date", "<", limit_date)])
        count = len(stale)
        stale.unlink()
        return count

    def action_open_connection(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": "bwatech.connection",
            "res_id": self.connection_id.id,
            "view_mode": "form",
            "target": "current",
            "name": _("BWATECH Connection"),
        }

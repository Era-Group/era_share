# -*- coding: utf-8 -*-
"""Manual backfill from Sembly MCP.

The webhook channel only ever sees FUTURE meetings, so this wizard is the only
way to bring history in. Manager-only.
"""
from datetime import date, timedelta

from odoo import _, fields, models
from odoo.exceptions import UserError

from ..services.sembly_mcp_client import SemblyMcpError


class SemblyImportWizard(models.TransientModel):
    _name = 'sembly.import.wizard'
    _description = "Import Meetings from Sembly"

    date_from = fields.Date(
        string="من تاريخ", default=lambda self: date.today() - timedelta(days=30))
    date_to = fields.Date(string="إلى تاريخ", default=lambda self: date.today())
    title_contains = fields.Char(string="العنوان يحتوي")
    limit = fields.Integer(string="الحد الأقصى", default=50,
                           help="Sembly caps this at 200 per call.")
    fetch_details = fields.Boolean(
        string="جلب التفاصيل والملخص", default=True,
        help="Calls get_meeting per meeting — slower, but this is what brings "
             "the summary and the structured items.")
    run_ai_match = fields.Boolean(
        string="ربط بالذكاء الاصطناعي بعد الاستيراد", default=False)

    def action_test_connection(self):
        """Reaches the MCP server. Succeeds even with no token — that is the
        point: it separates 'network broken' from 'credential missing'."""
        self.ensure_one()
        client = self.env['sembly.meeting'].sudo()._get_client()
        try:
            tools = client.list_tools()
        except SemblyMcpError as exc:
            raise UserError(_("Could not reach the Sembly MCP server: %s", exc)) from exc
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _("Sembly"),
                'message': _("Server reachable. Tools: %s.%s",
                             ", ".join(t.get('name') or '?' for t in tools),
                             '' if client.token else _(" No token configured yet.")),
                'type': 'success', 'sticky': True,
            },
        }

    def action_import(self):
        self.ensure_one()
        Meeting = self.env['sembly.meeting'].sudo()
        client = Meeting._get_client()
        if not client.token:
            raise UserError(_(
                "No Sembly MCP token is configured. Set it in Settings → Sembly "
                "(or the SEMBLY_MCP_TOKEN environment variable) first."))

        try:
            listed = client.list_meetings(
                from_date=self.date_from.isoformat() if self.date_from else None,
                to_date=self.date_to.isoformat() if self.date_to else None,
                title=self.title_contains or None,
                limit=self.limit or 50)
        except SemblyMcpError as exc:
            raise UserError(_("Sembly import failed: %s", exc)) from exc

        imported = Meeting.browse()
        errors = []
        for meta in listed:
            try:
                details = client.get_meeting(meta['id']) if self.fetch_details else None
                imported |= Meeting._upsert_from_mcp(meta, details)
                # Commit per meeting. This import runs inside an HTTP worker, so
                # it is killed at limit_time_real (240s here) — without this,
                # a run that fetched 90 meetings and then timed out saved none
                # of them. For a whole history use action_start_backfill instead.
                if Meeting._may_commit():
                    self.env.cr.commit()
            except SemblyMcpError as exc:
                errors.append("%s: %s" % (meta.get('id'), exc))

        self.env['sembly.sync.log']._log(
            'mcp', 'wizard-import', 'error' if errors else 'ok',
            "\n".join(errors) if errors else "imported %s" % len(imported),
            meeting_count=len(imported))

        if imported and self.run_ai_match:
            imported._ai_match()

        return {
            'type': 'ir.actions.act_window',
            'name': _("الاجتماعات المستوردة"),
            'res_model': 'sembly.meeting',
            'view_mode': 'list,form',
            'domain': [('id', 'in', imported.ids)],
        }

    def action_start_backfill(self):
        """Import the WHOLE history, in the background.

        One get_meeting round trip per meeting means a real history runs for
        many minutes; an HTTP worker is killed at limit_time_real long before
        that, and until this existed the answer was a timeout and nothing
        saved. So this only arms the cron and returns immediately — the cron
        walks backwards in windows, commits per meeting, and resumes where it
        left off, however many ticks that takes.
        """
        self.ensure_one()
        Meeting = self.env['sembly.meeting'].sudo()
        if not Meeting._get_client().token:
            raise UserError(_(
                "No Sembly MCP token is configured. Set it in Settings → Sembly "
                "(or the SEMBLY_MCP_TOKEN environment variable) first."))

        Meeting._start_backfill(date_from=self.date_from, date_to=self.date_to)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _("Sembly"),
                'message': _(
                    "Historical import started in the background. It runs every "
                    "5 minutes and resumes where it stops, so you can close this "
                    "window. Follow it in الإعدادات → سجل المزامنة."),
                'type': 'success', 'sticky': True,
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }

    def action_stop_backfill(self):
        """Stop the background backfill and disarm its cron.

        Lives HERE rather than in the cron because a cron worker holds a row
        lock on its own ir_cron row in a separate transaction, so a job that
        deactivates itself hangs. An HTTP request has no such lock.
        """
        self.ensure_one()
        Meeting = self.env['sembly.meeting'].sudo()
        self.env['ir.config_parameter'].sudo().set_param(
            'sembly.backfill_state', 'idle')
        disarmed = Meeting._disarm_backfill_cron()
        return {
            'type': 'ir.actions.client', 'tag': 'display_notification',
            'params': {
                'title': _("Sembly"), 'type': 'success',
                'message': _("Historical import stopped.%s",
                             _(" Its scheduled action is disarmed.") if disarmed else ""),
            },
        }

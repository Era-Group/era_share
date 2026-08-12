# -*- coding: utf-8 -*-
"""Audit trail for every sync run and webhook hit (CLAUDE.md rule 20).

Kept deliberately thin and manager-only: it exists so that a silent failure —
a rotated token, a Sembly outage, a rejected webhook — is visible after the
fact instead of only living in the server log. Purged after 90 days.
"""
from odoo import api, fields, models


class SemblySyncLog(models.Model):
    _name = 'sembly.sync.log'
    _description = "Sembly Sync Log"
    _order = 'create_date desc, id desc'

    channel = fields.Selection(
        [('mcp', "MCP"), ('webhook', "Webhook"), ('ai', "AI"),
         ('google', "Google")],
        string="القناة", required=True, index=True)
    operation = fields.Char(string="العملية", required=True)
    state = fields.Selection(
        [('ok', "نجاح"), ('error', "خطأ")],
        string="الحالة", required=True, default='ok', index=True)
    message = fields.Text(string="الرسالة")
    meeting_count = fields.Integer(string="عدد الاجتماعات")
    duration_ms = fields.Integer(string="المدة (مللي ثانية)")
    remote_ip = fields.Char(string="عنوان IP", help="Webhook calls only.")

    @api.model
    def _log(self, channel, operation, state='ok', message=None,
             meeting_count=0, duration_ms=0, remote_ip=None):
        """Create one audit row.

        Never raises: a logging failure must not abort the work being logged,
        and this is called from cron and webhook paths where an exception would
        lose the whole run.
        """
        try:
            return self.sudo().create({
                'channel': channel,
                'operation': operation,
                'state': state,
                'message': (message or '')[:4000] or False,
                'meeting_count': meeting_count,
                'duration_ms': duration_ms,
                'remote_ip': remote_ip or False,
            })
        except Exception:  # noqa: BLE001 - deliberately swallowed, see docstring
            return self.browse()

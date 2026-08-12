# -*- coding: utf-8 -*-
"""Settings → Sembly → Google.

The service-account JSON is a private key, so it follows exactly the same
write-only pattern as the Sembly MCP token: the getter returns a mask, and
saving the mask unchanged keeps the stored value (CLAUDE.md rule 03).
"""
import json

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..services.google_workspace_client import GoogleWorkspaceError

SA_PARAM = 'sembly.google_service_account'


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    sembly_google_enabled = fields.Boolean(
        string="تفعيل Google كمزوّد", config_parameter='sembly.google_enabled',
        help="Google can run alongside Sembly, instead of it, or not at all — "
             "the two providers are independent. Off by default, so nothing "
             "reaches Google until this is turned on.")
    sembly_google_service_account = fields.Text(
        string="مفتاح حساب الخدمة (JSON)",
        help="The whole service-account JSON from Google Cloud. Stored "
             "write-only: it holds a private key, so the field shows a mask "
             "once set and saving the mask unchanged keeps it.")
    sembly_google_sa_is_set = fields.Boolean(
        string="المفتاح مُعرَّف", compute='_compute_google_sa_state')
    sembly_google_sa_email = fields.Char(
        string="بريد حساب الخدمة", compute='_compute_google_sa_state',
        help="Authorise THIS address for domain-wide delegation in the "
             "Workspace admin console.")
    sembly_google_subject = fields.Char(
        string="المستخدم الافتراضي للانتحال", config_parameter='sembly.google_subject',
        help="Used for calls that are not about one person's Drive — a "
             "Workspace admin address is the usual choice.")
    sembly_google_subjects = fields.Char(
        string="حسابات محددة للمسح", config_parameter='sembly.google_subjects',
        help="Comma separated. Leave EMPTY to sweep ONLY the account above, "
             "which is normally enough: files.list returns everything that "
             "account can SEE, including files other people shared with it. "
             "Measured here — 1,108 recordings are visible to crm@era.net.sa "
             "while it owns only 77. Add a second address only if it holds "
             "recordings nobody shared.")
    sembly_google_window_days = fields.Integer(
        string="نافذة مسح Drive (أيام)", config_parameter='sembly.google_window_days')
    sembly_google_match_minutes = fields.Integer(
        string="تسامح المطابقة الزمني (دقائق)",
        config_parameter='sembly.google_match_minutes',
        help="How far a recording's creation time may sit from a Sembly "
             "meeting's start and still be judged the same meeting. Recording "
             "begins when a human presses record, always a little late.")
    sembly_google_translate_notes = fields.Boolean(
        string="ملخص عربي موحّد من المصدرين",
        config_parameter='sembly.google_translate_notes',
        help="Produces ONE Arabic summary per meeting. When Sembly also has a "
             "summary the two are MERGED in a single call, because each "
             "provider covers what the other missed — Sembly extracts "
             "decisions, tasks and risks, Gemini writes prose over Google's "
             "transcript. When Sembly has nothing, Google's notes are simply "
             "translated. Gemini writes in English even for an Arabic meeting, "
             "so an Arabic output is needed either way.")

    sembly_google_backfill_state = fields.Char(
        string="حالة سحب السجل", compute='_compute_google_backfill_state')

    def _compute_google_backfill_state(self):
        icp = self.env['ir.config_parameter'].sudo()
        state = icp.get_param('sembly.google_backfill_state') or 'idle'
        seen = icp.get_param('sembly.google_backfill_seen') or '0'
        matched = icp.get_param('sembly.google_backfill_matched') or '0'
        notes = icp.get_param('sembly.google_notes_state') or 'idle'
        label = {'idle': "لم يبدأ", 'running': "يعمل", 'done': "اكتمل",
                 'error': "توقّف بخطأ"}
        text = "التسجيلات: %s (%s تسجيلاً، %s مطابقاً) · الملاحظات: %s" % (
            label.get(state, state), seen, matched, label.get(notes, notes))
        for record in self:
            record.sembly_google_backfill_state = text

    def action_google_start_backfill(self):
        self.ensure_one()
        self.env['sembly.meeting'].sudo()._start_google_backfill()
        return {
            'type': 'ir.actions.client', 'tag': 'display_notification',
            'params': {'title': _("Google"), 'type': 'success', 'sticky': True,
                       'message': _(
                           "Drive sweep started. It pages through the whole "
                           "history in the background and costs no AI call — "
                           "follow it in سجل المزامنة.")},
        }

    def action_google_start_notes_backfill(self):
        self.ensure_one()
        self.env['sembly.meeting'].sudo()._start_google_notes_backfill()
        return {
            'type': 'ir.actions.client', 'tag': 'display_notification',
            'params': {'title': _("Google"), 'type': 'success', 'sticky': True,
                       'message': _(
                           "Gemini notes import started. This one DOES spend "
                           "one AI call per meeting, to merge or translate.")},
        }

    # ------------------------------------------------------------------ secret
    @api.model
    def _google_sa_mask(self, raw):
        if not raw:
            return ''
        try:
            email = json.loads(raw).get('client_email') or ''
        except ValueError:
            email = ''
        return '•••••• %s' % email if email else '•' * 12

    def _compute_google_sa_state(self):
        raw = self.env['ir.config_parameter'].sudo().get_param(SA_PARAM) or ''
        email = ''
        if raw:
            try:
                email = json.loads(raw).get('client_email') or ''
            except ValueError:
                email = ''
        for record in self:
            record.sembly_google_sa_is_set = bool(raw)
            record.sembly_google_sa_email = email

    @api.model
    def get_values(self):
        values = super().get_values()
        raw = self.env['ir.config_parameter'].sudo().get_param(SA_PARAM) or ''
        values['sembly_google_service_account'] = self._google_sa_mask(raw)
        return values

    def set_values(self):
        super().set_values()
        icp = self.env['ir.config_parameter'].sudo()
        submitted = (self.sembly_google_service_account or '').strip()
        current = icp.get_param(SA_PARAM) or ''
        if submitted and submitted != self._google_sa_mask(current):
            try:
                parsed = json.loads(submitted)
            except ValueError as exc:
                raise UserError(_("That is not valid JSON: %s", exc)) from exc
            if not parsed.get('client_email') or not parsed.get('private_key'):
                raise UserError(_(
                    "This JSON has no client_email/private_key — it does not "
                    "look like a service-account key."))
            icp.set_param(SA_PARAM, submitted)
        elif not submitted and current:
            icp.set_param(SA_PARAM, '')

    # ----------------------------------------------------------------- actions
    def action_sembly_google_test(self):
        """Prove the credentials AND the delegation, and say which failed.

        Testing the two separately matters: the key can be perfectly valid
        while domain-wide delegation has not been authorised, and the error
        Google returns for that is otherwise baffling.
        """
        self.ensure_one()
        Meeting = self.env['sembly.meeting'].sudo()
        subject = (self.sembly_google_subject or '').strip() or None
        try:
            user = Meeting._google_client(subject=subject).check()
        except GoogleWorkspaceError as exc:
            hint = ""
            if 'unauthorized_client' in str(exc) or 'invalid_grant' in str(exc):
                hint = _("\n\nThis usually means domain-wide delegation has not "
                         "been authorised for this service account, or the "
                         "impersonated address does not exist.")
            raise UserError(_("Google rejected the connection: %s%s", exc, hint)) from exc
        return {
            'type': 'ir.actions.client', 'tag': 'display_notification',
            'params': {
                'title': _("Google"), 'type': 'success', 'sticky': True,
                'message': _("Connected as %(email)s%(who)s.",
                             email=user.get('emailAddress') or '?',
                             who=_(" (impersonating %s)", subject) if subject else ""),
            },
        }

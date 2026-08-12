# -*- coding: utf-8 -*-
"""Settings → Sembly.

Every behavioural value is an ``ir.config_parameter`` under the ``sembly.*``
namespace. The token is the exception to the plain ``config_parameter``
pattern: it is WRITE-ONLY. Its getter returns a mask, so the secret can never
be read back through the ORM/UI (CLAUDE.md rule 03), and a blank or masked
submission leaves the stored value untouched.
"""
from datetime import datetime, timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..services.sembly_mcp_client import SemblyMcpError

TOKEN_PARAM = 'sembly.mcp_token'
TOKEN_SET_ON_PARAM = 'sembly.mcp_token_set_on'
ROTATION_DAYS = 90  # CLAUDE.md rule 04


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    sembly_region = fields.Selection(
        [('us', "US (mcp.sembly.ai)"), ('eu', "EU (mcp-eu.sembly.ai)")],
        string="منطقة Sembly", default='us', config_parameter='sembly.region',
        help="Picks the MCP host. Sembly offers US and EU only; US is Sembly's default.")
    sembly_mcp_url = fields.Char(
        string="رابط MCP (تجاوز)", config_parameter='sembly.mcp_url',
        help="Leave empty to derive the URL from the region.")
    sembly_mcp_token = fields.Char(
        string="رمز Sembly MCP",
        help="Created in Sembly under My Automations → MCP. Stored write-only: "
             "the field shows a mask once set, and saving the mask unchanged "
             "keeps the existing token. The SEMBLY_MCP_TOKEN environment "
             "variable, when present, takes precedence over this value.")
    sembly_token_is_set = fields.Boolean(
        string="الرمز مُعرَّف", compute='_compute_sembly_token_state')
    sembly_token_rotation_due = fields.Boolean(
        string="يجب تدوير الرمز", compute='_compute_sembly_token_state')
    sembly_webhook_url = fields.Char(
        string="رابط الويب هوك", compute='_compute_sembly_webhook_url',
        help="Paste this into Sembly → Automations → Custom → New Automation, "
             "once for Transcription and once for Notes.")

    sembly_meeting_url_template = fields.Char(
        string="قالب رابط الاجتماع", config_parameter='sembly.meeting_url_template')
    sembly_share_url_template = fields.Char(
        string="قالب رابط المشاركة", config_parameter='sembly.share_url_template',
        help="Used only when a payload hands us a meeting UUID: {token} is that "
             "UUID base64-encoded. It can never be built from the numeric "
             "meeting id, which is all the MCP tools return.")
    # The bucket-task settings are NOT here: they belong to
    # era_sembly_meetings_tasks, which adds them into the same Sembly app block.
    sembly_ai_agent_id = fields.Many2one(
        'ai.agent', string="وكيل الذكاء الاصطناعي",
        config_parameter='sembly.ai_agent_id',
        help="The agent that proposes the opportunity / task / ticket. Odoo "
             "stores the id in the sembly.ai_agent_id parameter, so the model "
             "side is unchanged — this is only a nicer way to choose it. The "
             "transcript is NEVER sent: only the title, the date, the "
             "participant names and the summary.")
    sembly_ai_brief_enabled = fields.Boolean(
        string="تلخيص تنفيذي قبل النشر",
        config_parameter='sembly.ai_brief_enabled',
        help="Before the summary is posted on the opportunity/task/ticket, the "
             "AI agent condenses it into: client situation, positives, "
             "negatives/risks, scope of work, next step. If generation fails "
             "the raw summary is posted, so the note is never blocked.")
    sembly_internal_fallback_ref = fields.Reference(
        selection='_sembly_fallback_models', string="سجل الاجتماعات الداخلية",
        compute='_compute_sembly_internal_fallback_ref', inverse='_inverse_sembly_internal_fallback_ref',
        help="Where meetings that look like INTERNAL team meetings are filed "
             "when nothing else in the system claims them. Leave empty to "
             "disable: with no target configured nothing is evaluated, so the "
             "feature costs no AI call at all.")
    sembly_internal_domains = fields.Char(
        string="نطاقات داخلية إضافية", config_parameter='sembly.internal_domains',
        help="Comma separated. Our own domains are derived from the company "
             "and its internal users automatically; this is for what "
             "derivation cannot see, such as a subsidiary's domain. Public "
             "providers (gmail, outlook, …) are ignored on both sides.")
    sembly_ignored_domains = fields.Char(
        string="نطاقات محايدة (تُستبعد من التقييم)",
        config_parameter='sembly.ignored_domains',
        help="Comma separated. Treated exactly like gmail/outlook: they count "
             "neither for nor against a meeting being internal. Put the SEMBLY "
             "workspace account's own domain here — it attends every meeting, "
             "and if it is on neither your domain nor a public provider it "
             "would otherwise mark every single meeting as external.")
    sembly_ai_confidence_threshold = fields.Float(
        string="حد الثقة للربط الآلي", config_parameter='sembly.ai_confidence_threshold',
        help="At or above this, the link is applied automatically. Below it, the "
             "meeting shows a suggestion with a one-click apply.")
    sembly_sync_window_days = fields.Integer(
        string="نافذة المزامنة (أيام)", config_parameter='sembly.sync_window_days')
    sembly_sync_batch_size = fields.Integer(
        string="حجم دفعة المزامنة", config_parameter='sembly.sync_batch_size')
    sembly_rematch_after_days = fields.Integer(
        string="إعادة البحث عن ربط بعد (أيام)",
        config_parameter='sembly.rematch_after_days',
        help="How long before an unlinked meeting is offered to the matcher "
             "again. The answer can change — the opportunity a meeting belongs "
             "to is often created after the meeting itself. Without a cooldown "
             "the cron never goes quiet and pays to re-derive the same 'no "
             "match' from unchanged data. Set to -1 to stop re-searching.")
    sembly_match_batch_size = fields.Integer(
        string="حجم دفعة المطابقة", config_parameter='sembly.match_batch_size')
    sembly_raw_retention_days = fields.Integer(
        string="مدة الاحتفاظ بالحمولة الخام (أيام)",
        config_parameter='sembly.raw_retention_days')
    sembly_timezone = fields.Char(
        string="المنطقة الزمنية", config_parameter='sembly.timezone',
        help="Used for the 'today or yesterday' window when posting the latest "
             "meeting summary to the chatter.")

    # ------------------------------------------------------------- backfill
    sembly_backfill_state = fields.Char(
        string="حالة استيراد سجل Sembly", compute='_compute_sembly_backfill_state')

    def _compute_sembly_backfill_state(self):
        icp = self.env['ir.config_parameter'].sudo()
        label = {'idle': "لم يبدأ", 'running': "يعمل", 'done': "اكتمل",
                 'error': "توقّف بخطأ"}
        state = icp.get_param('sembly.backfill_state') or 'idle'
        text = "%s · %s اجتماعاً · وصل إلى %s" % (
            label.get(state, state),
            icp.get_param('sembly.backfill_imported') or '0',
            icp.get_param('sembly.backfill_cursor') or '—')
        for record in self:
            record.sembly_backfill_state = text

    def action_sembly_start_backfill(self):
        """Same place as the Google import, because they are the same job.

        Lives here rather than in the import wizard: a whole-history import is
        a configuration decision taken once, not a per-use dialog, and having
        the two providers' imports side by side is what makes it obvious that
        both exist.
        """
        self.ensure_one()
        Meeting = self.env['sembly.meeting'].sudo()
        if not Meeting._get_client().token:
            raise UserError(_(
                "No Sembly MCP token is configured. Set it above first."))
        Meeting._start_backfill()
        return {
            'type': 'ir.actions.client', 'tag': 'display_notification',
            'params': {'title': _("Sembly"), 'type': 'success', 'sticky': True,
                       'message': _(
                           "Historical import started. It walks backwards in "
                           "windows, resumes where it stops, and ends by itself "
                           "when the history runs dry — follow it in سجل المزامنة.")},
        }

    def action_sembly_stop_backfill(self):
        self.ensure_one()
        self.env['ir.config_parameter'].sudo().set_param(
            'sembly.backfill_state', 'idle')
        disarmed = self.env['sembly.meeting'].sudo()._disarm_backfill_cron()
        return {
            'type': 'ir.actions.client', 'tag': 'display_notification',
            'params': {'title': _("Sembly"), 'type': 'success',
                       'message': _("Historical import stopped.%s",
                                    _(" Its scheduled action is disarmed.")
                                    if disarmed else "")},
        }

    # ------------------------------------------------------ internal fallback
    @api.model
    def _sembly_fallback_models(self):
        """Whatever the installed link modules contribute — so this offers
        Opportunity when CRM is installed, Task when Project is, and neither
        when nothing is."""
        models = self.env['sembly.meeting']._sembly_link_field_by_model()
        return [(name, self.env[name]._description) for name in sorted(models)]

    def _compute_sembly_internal_fallback_ref(self):
        ref = self.env['ir.config_parameter'].sudo().get_param(
            'sembly.internal_fallback_ref') or ''
        model, _sep, res_id = ref.partition(',')
        value = False
        if model and res_id and model in self.env:
            # A deleted target must not break the settings page.
            if self.env[model].sudo().browse(int(res_id)).exists():
                value = '%s,%s' % (model, res_id)
        for record in self:
            record.sembly_internal_fallback_ref = value

    def _inverse_sembly_internal_fallback_ref(self):
        icp = self.env['ir.config_parameter'].sudo()
        for record in self:
            value = record.sembly_internal_fallback_ref
            icp.set_param('sembly.internal_fallback_ref',
                          '%s,%s' % (value._name, value.id) if value else '')

    # ------------------------------------------------------------------ token
    @api.model
    def _token_mask(self, token):
        return ('•' * 6 + token[-6:]) if token and len(token) > 6 else ('•' * 8 if token else '')

    def _compute_sembly_token_state(self):
        icp = self.env['ir.config_parameter'].sudo()
        token = icp.get_param(TOKEN_PARAM) or ''
        set_on = icp.get_param(TOKEN_SET_ON_PARAM)
        due = False
        if token and set_on:
            try:
                due = fields.Datetime.from_string(set_on) < \
                    datetime.utcnow() - timedelta(days=ROTATION_DAYS)
            except (TypeError, ValueError):
                due = False
        for record in self:
            record.sembly_token_is_set = bool(token)
            record.sembly_token_rotation_due = bool(due)

    def _compute_sembly_webhook_url(self):
        icp = self.env['ir.config_parameter'].sudo()
        base = (icp.get_param('web.base.url') or '').rstrip('/')
        token = icp.get_param('sembly.webhook_token') or ''
        url = '%s/sembly/webhook/%s' % (base, token) if token else ''
        for record in self:
            record.sembly_webhook_url = url

    @api.model
    def get_values(self):
        values = super().get_values()
        token = self.env['ir.config_parameter'].sudo().get_param(TOKEN_PARAM) or ''
        # Never hand the real value to the client.
        values['sembly_mcp_token'] = self._token_mask(token)
        return values

    def set_values(self):
        super().set_values()
        icp = self.env['ir.config_parameter'].sudo()
        submitted = (self.sembly_mcp_token or '').strip()
        current = icp.get_param(TOKEN_PARAM) or ''
        if submitted and submitted != self._token_mask(current):
            icp.set_param(TOKEN_PARAM, submitted)
            icp.set_param(TOKEN_SET_ON_PARAM, fields.Datetime.to_string(datetime.utcnow()))
        elif not submitted and current:
            # An explicitly emptied field clears the token (deliberate removal).
            icp.set_param(TOKEN_PARAM, '')
            icp.set_param(TOKEN_SET_ON_PARAM, '')

    # ------------------------------------------------------------------ actions
    def action_sembly_test_connection(self):
        """Proves network reachability (tools/list needs no token), then, when a
        token is configured, proves the credential with a real tool call."""
        self.ensure_one()
        Meeting = self.env['sembly.meeting'].sudo()
        client = Meeting._get_client()
        try:
            tools = client.list_tools()
        except SemblyMcpError as exc:
            raise UserError(_("Could not reach the Sembly MCP server: %s", exc)) from exc

        names = ", ".join(t.get('name') or '?' for t in tools)
        if not client.token:
            message = _("Reachable. Tools: %s.\nNo token configured yet, so no "
                        "meeting data can be pulled.", names)
        else:
            try:
                found = client.list_meetings(limit=1)
            except SemblyMcpError as exc:
                raise UserError(_("Token rejected by Sembly: %s", exc)) from exc
            message = _("Connected. Tools: %s.\nToken %s accepted; %s recent "
                        "meeting(s) visible.", names, client.token_hint, len(found))
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {'title': _("Sembly"), 'message': message,
                       'type': 'success', 'sticky': True},
        }

# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.tools import html_escape


class CrmRealtimeCallSummary(models.Model):
    _name = "crm.realtime_call_summary"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _description = "ملخص مكالمة فورية"
    _order = "create_date desc"

    name = fields.Char(required=True, default=lambda self: _("ملخص مكالمة فورية"))
    call_source = fields.Selection(
        [
            ("incoming", "واردة"),
            ("outgoing", "صادرة"),
            ("agent", "وكيل"),
        ],
        default="agent",
        required=True,
    )
    summary = fields.Text(required=True)
    transcription = fields.Text()
    transcript = fields.Text()
    prompt_id = fields.Char()
    prompt_version = fields.Char()
    model = fields.Char()
    voice = fields.Char()
    duration_seconds = fields.Integer(string="ثوانٍ")
    attachment_id = fields.Many2one("ir.attachment", string="التسجيل")
    attachment_datas = fields.Binary(related="attachment_id.datas", readonly=True)
    attachment_mimetype = fields.Char(related="attachment_id.mimetype", readonly=True)
    attachment_name = fields.Char(string="اسم ملف التسجيل", related="attachment_id.name", readonly=True)
    recording_link_html = fields.Html(
        string="رابط التسجيل",
        compute="_compute_recording_link_html",
    )
    salesperson_user_id = fields.Many2one("res.users", string="مندوب المبيعات", index=True)
    lead_id = fields.Many2one("crm.lead", string="عميل محتمل/فرصة")
    caller_phone = fields.Char()
    caller_company = fields.Char()
    caller_ip = fields.Char(index=True)
    is_active = fields.Boolean(default=False, copy=False, index=True)
    session_key = fields.Char(copy=False, index=True)
    client_call_id = fields.Char(copy=False, index=True)

    _client_call_id_uniq = models.Constraint(
        "unique(client_call_id)",
        "Client call id must be unique.",
    )

    @api.depends("attachment_id", "attachment_name")
    def _compute_recording_link_html(self):
        for record in self:
            if record.attachment_id:
                filename = html_escape(record.attachment_name or "realtime-call.webm")
                record.recording_link_html = (
                    f'<a href="/realtime_agent/recording_player/{record.id}" target="_blank">{filename}</a>'
                )
            else:
                record.recording_link_html = ""

    def _create_done_call_activity(self, lead, summary, attachment=None):
        if not lead:
            return None
        ActivityType = self.env["mail.activity.type"].sudo()
        activity_type = ActivityType.search([("category", "=", "call")], limit=1)
        if not activity_type:
            activity_type = ActivityType.search([("name", "ilike", "call")], limit=1)
        if not activity_type:
            activity_type = ActivityType.search([], limit=1)
        if not activity_type:
            return None

        Model = self.env["ir.model"].sudo()
        model_record = Model._get("crm.lead") if hasattr(Model, "_get") else Model.search([("model", "=", "crm.lead")], limit=1)
        if not model_record:
            return None

        link_html = ""
        base_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url", "") or ""
        if attachment:
            path = f"/realtime_agent/recording_player/{self.id}"
            url = f"{base_url}{path}" if base_url else path
            filename = html_escape(attachment.name or "realtime-call.webm")
            link_html = f'<p><a href="{url}" target="_blank">{filename}</a></p>'

        summary_html = f"{summary}" if summary else ""

        activity = self.env["mail.activity"].sudo().create(
            {
                "activity_type_id": activity_type.id,
                "res_model_id": model_record.id,
                "res_id": lead.id,
                "user_id": self.env.user.id,
                "summary": self.call_source or "call",
                "note": link_html,
            }
        )

        feedback = summary_html
        try:
            if hasattr(activity, "action_feedback"):
                try:
                    activity.action_feedback(feedback)
                except TypeError:
                    activity.action_feedback(feedback)
            elif hasattr(activity, "action_done"):
                try:
                    activity.action_done(feedback)
                except TypeError:
                    activity.action_done(feedback)
        except Exception:
            lead.message_post(body=feedback, subtype_xmlid="mail.mt_note")
        return activity

    def _create_salesperson_assignment_activity(self):
        ActivityType = self.env["mail.activity.type"].sudo()
        activity_type = ActivityType.search([("category", "=", "default")], limit=1)
        if not activity_type:
            activity_type = ActivityType.search([("name", "ilike", "to-do")], limit=1)
        if not activity_type:
            activity_type = ActivityType.search([], limit=1)
        if not activity_type:
            return

        Model = self.env["ir.model"].sudo()
        model_record = (
            Model._get(self._name)
            if hasattr(Model, "_get")
            else Model.search([("model", "=", self._name)], limit=1)
        )
        if not model_record:
            return

        base_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url", "") or ""
        for record in self:
            if not record.salesperson_user_id:
                continue
            call_link = ""
            if base_url and record.id:
                call_link = (
                    f"{base_url}/web#id={record.id}"
                    f"&model=crm.realtime_call_summary&view_type=form"
                )
            summary_line = (record.summary or "").strip().splitlines()
            summary_preview = summary_line[0] if summary_line else ""
            caller = record.caller_phone or record.caller_company or "-"
            note = (
                f"<p>New website call assigned to you.</p>"
                f"<p><b>Caller:</b> {html_escape(caller)}</p>"
                f"<p><b>Summary:</b> {html_escape(summary_preview)}</p>"
            )
            if call_link:
                note += f'<p><a href="{call_link}" target="_blank">Open call summary</a></p>'

            self.env["mail.activity"].sudo().create(
                {
                    "activity_type_id": activity_type.id,
                    "res_model_id": model_record.id,
                    "res_id": record.id,
                    "user_id": record.salesperson_user_id.id,
                    "summary": _("Website call assigned"),
                    "note": note,
                }
            )

    def _notify_salesperson_assignment_change(self, previous_salespersons):
        for record in self:
            old_user_id = previous_salespersons.get(record.id)
            new_user_id = record.salesperson_user_id.id if record.salesperson_user_id else False
            if new_user_id and new_user_id != old_user_id:
                record._create_salesperson_assignment_activity()

    @api.model_create_multi
    def create(self, vals_list):
        name_prefix = _("ملخص مكالمة فورية")
        for vals in vals_list:
            if not vals.get("name"):
                vals["name"] = fields.Datetime.now().strftime(f"{name_prefix} %Y-%m-%d %H:%M:%S")
        records = super().create(vals_list)
        previous_salespersons = {record.id: False for record in records}
        records._notify_salesperson_assignment_change(previous_salespersons)
        return records

    def write(self, vals):
        previous_salespersons = {record.id: record.salesperson_user_id.id for record in self}
        result = super().write(vals)
        if "salesperson_user_id" in vals:
            self._notify_salesperson_assignment_change(previous_salespersons)
        return result

    def action_open_recording_player(self):
        self.ensure_one()
        record = self.sudo()
        if not record.attachment_id:
            return False
        return {
            "type": "ir.actions.act_url",
            "url": f"/realtime_agent/recording_player/{record.id}",
            "target": "new",
        }

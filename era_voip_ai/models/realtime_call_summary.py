# -*- coding: utf-8 -*-
from odoo import api, fields, models
from odoo.tools import html_escape


class CrmRealtimeCallSummary(models.Model):
    _name = "crm.realtime_call_summary"
    _description = "Realtime Call Summary"
    _order = "create_date desc"

    name = fields.Char(required=True, default=lambda self: "Realtime Call Summary")
    call_source = fields.Selection(
        [
            ("incoming", "Incoming"),
            ("outgoing", "Outgoing"),
            ("agent", "Agent"),
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
    duration_seconds = fields.Integer(string="Seconds")
    attachment_id = fields.Many2one("ir.attachment", string="Recording")
    attachment_datas = fields.Binary(related="attachment_id.datas", readonly=True)
    attachment_mimetype = fields.Char(related="attachment_id.mimetype", readonly=True)
    attachment_name = fields.Char(related="attachment_id.name", readonly=True)
    recording_link_html = fields.Html(
        string="Recording Link",
        compute="_compute_recording_link_html",
    )
    lead_id = fields.Many2one("crm.lead", string="Lead/Opportunity")
    caller_phone = fields.Char()
    caller_company = fields.Char()

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

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("name"):
                vals["name"] = fields.Datetime.now().strftime("Realtime Call Summary %Y-%m-%d %H:%M:%S")
        return super().create(vals_list)

    def write(self, vals):
        previous_leads = {record.id: record.lead_id.id for record in self}
        result = super().write(vals)
        if vals.get("lead_id"):
            for record in self:
                if not previous_leads.get(record.id) and record.lead_id:
                    record._create_done_call_activity(
                        record.lead_id,
                        record.summary or "",
                        record.attachment_id,
                    )
        return result

    def action_open_recording_player(self):
        self.ensure_one()
        if not self.attachment_id:
            return False
        wizard = self.env["crm.realtime_call_summary_play_wizard"].create(
            {"attachment_id": self.attachment_id.id}
        )
        return {
            "type": "ir.actions.act_window",
            "name": "Call Recording",
            "res_model": "crm.realtime_call_summary_play_wizard",
            "view_mode": "form",
            "view_id": self.env.ref(
                "era_voip_ai.view_crm_realtime_call_summary_play_wizard_form"
            ).id,
            "res_id": wizard.id,
            "target": "new",
        }

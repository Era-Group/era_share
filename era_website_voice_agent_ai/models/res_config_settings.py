# -*- coding: utf-8 -*-
from odoo import api, fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    openai_api_key = fields.Char(
        string="OpenAI API Key",
        config_parameter="openai.api_key",
        help="Stored as system parameter: openai.api_key. Never expose this key to the browser.",
    )

    openai_realtime_prompt_id = fields.Char(
        string="Realtime Prompt ID (pmpt_...)",
        config_parameter="openai.realtime_prompt_id",
        help="The published Prompt ID for your Realtime agent, e.g. pmpt_... (Dashboard → Prompts).",
    )

    openai_realtime_prompt_version = fields.Char(
        string="Realtime Prompt Version",
        config_parameter="openai.realtime_prompt_version",
        help="Optional prompt version to use for Realtime sessions (e.g. 2).",
    )

    openai_realtime_model = fields.Char(
        string="Realtime Model",
        default="gpt-realtime",
        config_parameter="openai.realtime_model",
        help="Model used for Realtime sessions (default: gpt-realtime).",
    )

    openai_realtime_voice = fields.Char(
        string="Voice",
        default="alloy",
        config_parameter="openai.realtime_voice",
        help="Voice name for audio output (default: alloy).",
    )

    openai_realtime_widget_enabled = fields.Boolean(
        string="Show Website Widget",
        default=True,
        config_parameter="openai.realtime_widget_enabled",
        help="Show or hide the website floating widget.",
    )

    openai_realtime_summary_prompt = fields.Char(
        string="Summary Prompt",
        default="لخص المكالمة بالعربية بشكل قصير ومباشر جدًا. 2-3 نقاط كحد أقصى، واذكر أي إجراء مطلوب إن وجد. (المتصل هو العميل)",
        config_parameter="openai.realtime_summary_prompt",
        help="System prompt used when summarizing and analyzing recorded calls.",
    )

    @api.model
    def get_values(self):
        res = super().get_values()
        ICP = self.env["ir.config_parameter"].sudo()
        raw = ICP.get_param("openai.realtime_widget_enabled", default="1")
        res["openai_realtime_widget_enabled"] = str(raw).lower() in ("1", "true", "yes", "y", "t")
        return res

    def set_values(self):
        res = super().set_values()
        ICP = self.env["ir.config_parameter"].sudo()
        ICP.set_param(
            "openai.realtime_widget_enabled",
            "1" if self.openai_realtime_widget_enabled else "0",
        )
        return res

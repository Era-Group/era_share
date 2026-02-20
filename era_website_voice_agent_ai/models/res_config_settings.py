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
        default="gpt-realtime-mini",
        config_parameter="openai.realtime_model",
        help="Model used for Realtime sessions (default: gpt-realtime-mini).",
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

    openai_realtime_widget_label = fields.Char(
        string="Widget Button Label",
        config_parameter="openai.realtime_widget_label",
        help="Text shown on the floating website widget button.",
    )

    openai_realtime_summary_prompt = fields.Char(
        string="Summary Prompt",
        default="لخص المكالمة بالعربية بشكل قصير ومباشر جدًا. 2-3 نقاط كحد أقصى، واذكر أي إجراء مطلوب إن وجد. (المتصل هو العميل)",
        config_parameter="openai.realtime_summary_prompt",
        help="System prompt used when summarizing and analyzing recorded calls.",
    )
    openai_realtime_embed_allowed_origins = fields.Char(
        string="Allowed Embed Origins",
        config_parameter="openai.realtime_embed_allowed_origins",
        help=(
            "Optional allowlist of parent website origins allowed to embed the external widget iframe. "
            "Use one origin per line, e.g. https://example.com"
        ),
    )
    openai_realtime_embed_script = fields.Text(
        string="External Embed Script",
        compute="_compute_openai_realtime_embed_script",
        readonly=True,
        help="Copy/paste this script into any external website to show the floating voice widget.",
    )

    @api.depends(
        "openai_realtime_model",
        "openai_realtime_voice",
        "openai_realtime_widget_label",
        "openai_realtime_prompt_id",
    )
    def _compute_openai_realtime_embed_script(self):
        ICP = self.env["ir.config_parameter"].sudo()
        base_url = (ICP.get_param("web.base.url") or "").strip().rstrip("/")
        for rec in self:
            model = (rec.openai_realtime_model or "gpt-realtime-mini").strip()
            voice = (rec.openai_realtime_voice or "alloy").strip()
            label = (rec.openai_realtime_widget_label or "").strip()
            prompt_id = (rec.openai_realtime_prompt_id or "").strip()

            lines = [
                '<script',
                f'  src="{base_url}/era_website_voice_agent_ai/static/src/js/realtime_agent_embed_loader.js"',
                f'  data-base-url="{base_url}"',
                f'  data-model="{model}"',
                f'  data-voice="{voice}"',
            ]
            if label:
                lines.append(f'  data-label="{label}"')
            if prompt_id:
                lines.append(f'  data-prompt-id="{prompt_id}"')
            lines.extend(
                [
                    '  data-right="14"',
                    '  data-bottom="14"',
                    '  data-z-index="2147483000">',
                    "</script>",
                ]
            )
            rec.openai_realtime_embed_script = "\n".join(lines)

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

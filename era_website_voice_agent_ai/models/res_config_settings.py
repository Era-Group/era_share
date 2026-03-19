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
    openai_realtime_system_instructions_cfg = fields.Char(
        string="Realtime System Instructions",
        config_parameter="openai.realtime_system_instructions",
        help=(
            "System instructions sent directly to OpenAI Realtime sessions. "
            "If set, these instructions take precedence over Prompt ID instructions."
        ),
    )

    openai_realtime_interrupt_response_enabled = fields.Boolean(
        string="Allow Agent Interruption",
        default=False,
        config_parameter="openai.realtime_interrupt_response_enabled",
        help="If enabled, server VAD can interrupt the current assistant response when the user starts speaking.",
    )
    openai_realtime_vad_threshold = fields.Float(
        string="Realtime VAD Threshold",
        default=0.8,
        config_parameter="openai.realtime_vad_threshold",
        help=(
            "Voice activity detection threshold from 0.0 to 1.0. "
            "Higher values require louder speech and reduce false triggers from ambient noise."
        ),
    )
    openai_realtime_idle_timeout_seconds = fields.Integer(
        string="Realtime Idle Timeout (Seconds)",
        default=0,
        config_parameter="openai.realtime_idle_timeout_seconds",
        help=(
            "Silence timeout for Realtime sessions, in seconds. "
            "Set 0 to disable idle timeout."
        ),
    )
    openai_realtime_salesperson_user_id = fields.Many2one(
        "res.users",
        string="Assigned Salesperson",
        config_parameter="openai.realtime_salesperson_user_id",
        domain=[("share", "=", False)],
        help="Default salesperson assigned to new website call summaries.",
    )

    openai_realtime_widget_enabled = fields.Boolean(
        string="Show Widget Inside Website",
        default=True,
        config_parameter="openai.realtime_widget_enabled",
        help="Show or hide the floating widget on this Odoo website.",
    )

    openai_realtime_embed_enabled = fields.Boolean(
        string="Enable External Embed Widget",
        default=True,
        config_parameter="openai.realtime_embed_enabled",
        help="Allow or block the widget iframe for external websites using the embed script.",
    )

    openai_realtime_widget_label = fields.Char(
        string="Widget Button Label",
        config_parameter="openai.realtime_widget_label",
        help="Text shown on the floating website widget button.",
    )
    openai_realtime_auto_greet_enabled = fields.Boolean(
        string="Auto-Start Assistant Greeting",
        default=False,
        config_parameter="openai.realtime_auto_greet_enabled",
        help="If enabled, the Realtime assistant starts speaking automatically right after the connection opens.",
    )
    openai_realtime_auto_greet_instruction = fields.Text(
        string="Auto Greeting Instruction",
        config_parameter="openai.realtime_auto_greet_instruction",
        help=(
            "Instruction sent in the first response.create call after connect. "
            "Use this to control how the assistant opens the conversation."
        ),
    )
    openai_realtime_require_ptt = fields.Boolean(
        string="Require Push-to-Talk",
        default=True,
        config_parameter="openai.realtime_require_ptt",
        help=(
            "If enabled, the visitor must hold the microphone button to speak. "
            "Disable to keep the microphone live for the whole session."
        ),
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
        "openai_realtime_widget_label",
        "openai_realtime_auto_greet_enabled",
        "openai_realtime_auto_greet_instruction",
    )
    def _compute_openai_realtime_embed_script(self):
        ICP = self.env["ir.config_parameter"].sudo()
        base_url = (ICP.get_param("web.base.url") or "").strip().rstrip("/")
        for rec in self:
            label = (rec.openai_realtime_widget_label or "").strip()
            auto_greet_enabled = bool(rec.openai_realtime_auto_greet_enabled)
            auto_greet_instruction = (rec.openai_realtime_auto_greet_instruction or "").strip()

            lines = [
                '<script',
                f'  src="{base_url}/era_website_voice_agent_ai/static/src/js/realtime_agent_embed_loader.js"',
            ]
            if label:
                lines.append(f'  data-label="{label}"')
            if auto_greet_enabled:
                lines.append('  data-auto-greet="1"')
            if auto_greet_instruction:
                safe_instruction = auto_greet_instruction.replace('"', '&quot;').replace("\n", "&#10;")
                lines.append(f'  data-auto-greet-instruction="{safe_instruction}"')
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

        def _as_bool(raw_value, default=False):
            if raw_value in (None, ""):
                return default
            return str(raw_value).lower() in ("1", "true", "yes", "y", "t")

        raw_inside = ICP.get_param("openai.realtime_widget_enabled", default="1")
        raw_embed = ICP.get_param("openai.realtime_embed_enabled")
        if raw_embed in (None, ""):
            # Backward compatibility: until explicitly set, external embed follows the legacy single switch.
            raw_embed = raw_inside
        raw_auto_greet = ICP.get_param("openai.realtime_auto_greet_enabled", default="0")
        raw_require_ptt = ICP.get_param("openai.realtime_require_ptt", default="1")

        res["openai_realtime_widget_enabled"] = _as_bool(raw_inside, default=True)
        res["openai_realtime_embed_enabled"] = _as_bool(raw_embed, default=True)
        res["openai_realtime_auto_greet_enabled"] = _as_bool(raw_auto_greet, default=False)
        res["openai_realtime_require_ptt"] = _as_bool(raw_require_ptt, default=True)
        return res

    def set_values(self):
        res = super().set_values()
        ICP = self.env["ir.config_parameter"].sudo()
        ICP.set_param(
            "openai.realtime_widget_enabled",
            "1" if self.openai_realtime_widget_enabled else "0",
        )
        ICP.set_param(
            "openai.realtime_embed_enabled",
            "1" if self.openai_realtime_embed_enabled else "0",
        )
        ICP.set_param(
            "openai.realtime_auto_greet_enabled",
            "1" if self.openai_realtime_auto_greet_enabled else "0",
        )
        ICP.set_param(
            "openai.realtime_require_ptt",
            "1" if self.openai_realtime_require_ptt else "0",
        )
        return res

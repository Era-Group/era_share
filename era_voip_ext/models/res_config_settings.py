from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    era_voip_transcription_account_id = fields.Many2one(
        "era.ai.account",
        string="VoIP Transcription Account",
        config_parameter="era_voip_ext.transcription_account_id",
        domain="[('active', '=', True), ('provider', 'in', ('openai', 'assemblyai')), ('auth_mode', '=', 'api_key')]",
        help="AI account used for call speech-to-text. Use an OpenAI or AssemblyAI "
             "account with an API key so the key is not stored in the native AI "
             "settings field (which the PDPL compliance guard, Rule 03, forbids). "
             "Leave empty to fall back to the first OpenAI API-key account, then "
             "to the native key/environment.",
    )
    era_voip_text_account_id = fields.Many2one(
        "era.ai.account",
        string="VoIP Summary/Formatting Account",
        config_parameter="era_voip_ext.text_account_id",
        domain="[('active', '=', True), ('provider', '!=', 'assemblyai')]",
        help="AI account used to format the call transcript and to generate the "
             "one-line summary. API-key and local CLI chat accounts are supported. "
             "Leave empty to fall back to the first OpenAI API-key account, then "
             "to each agent's own account.",
    )

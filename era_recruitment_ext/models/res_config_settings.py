from odoo import api, fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    era_recruitment_ext_ai_agent_id = fields.Many2one(
        'ai.agent',
        string='AI Agent',
        config_parameter='era_recruitment_ext.ai_agent_id',
        help='AI agent used to analyze CVs.',
    )
    era_recruitment_ext_ai_prompt_template = fields.Text(
        string='AI Prompt Template',
        help=(
            'Template for the CV match prompt. Available placeholders: '
            '$applicant_name, $job_name, $job_description, $job_requirements, $cv_text, $user_language. '
            'Use $$ for a literal $.'
        ),
    )

    @api.model
    def get_values(self):
        res = super().get_values()
        res.update({
            'era_recruitment_ext_ai_prompt_template': self.env['ir.config_parameter'].sudo().get_param(
                'era_recruitment_ext.ai_prompt_template',
                default='',
            ),
        })
        return res

    def set_values(self):
        super().set_values()
        self.env['ir.config_parameter'].sudo().set_param(
            'era_recruitment_ext.ai_prompt_template',
            self.era_recruitment_ext_ai_prompt_template or '',
        )

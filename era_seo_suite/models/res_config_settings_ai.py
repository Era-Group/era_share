"""ERA SEO AI — settings page extension.

Exposes the AI Auto-Fix config: an enable flag and a picker for which
``ai.agent`` to use. The provider, model, and API key all live on the
agent (configured in the AI app), so there's nothing sensitive to store
here.
"""
import logging

from odoo import _, fields, models
from odoo.exceptions import UserError

from .ai_client import AIClient, AIUnavailable

_logger = logging.getLogger(__name__)


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    era_seo_ai_enabled = fields.Boolean(
        string='Enable AI Auto-Fix',
        config_parameter='era_seo.ai_enabled',
        help='Turn on "Suggest Fix" / "Apply Fix" on audit findings. When off '
             'the buttons hide and no AI calls happen.',
    )
    era_seo_ai_agent_id = fields.Many2one(
        'ai.agent',
        string='AI Agent',
        config_parameter='era_seo.ai_agent_id',
        help='Which AI agent answers SEO fix requests. The agent carries the '
             'provider, model, and API key (configured in the AI app). Leave '
             'empty to fall back to the site\'s "Ask AI" agent.',
    )

    def action_test_ai_agent(self):
        """Round-trip a tiny prompt through the configured agent."""
        self.ensure_one()
        client = AIClient(self.env)
        ok, reason = client.is_available()
        if not ok:
            raise UserError(reason)
        agent = client._resolve_agent()
        try:
            response = agent.get_direct_response(
                prompt='Reply with the single word: ok',
                context_message='You are a connectivity test. Reply with one word.',
            )
        except AIUnavailable as exc:
            raise UserError(str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise UserError(_('AI agent call failed: %s', exc)) from exc

        reply = (response[0] if response else '').strip()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'type': 'success',
                'message': _('Agent "%s" replied: %s', agent.name, reply[:120]),
                'sticky': False,
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }

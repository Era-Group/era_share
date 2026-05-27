"""ERA SEO AI — settings page extension.

Exposes the AI Auto-Fix configuration: enable flag, model, API key, and a
"Test API key" action that issues a one-token round trip so admins know
their credential works before they kick off a batch.

The API key is intentionally stored as an ICP parameter rather than a
plain field. Per CLAUDE.md §03 of the SEO Security Playbook ("Use
process.env keys"), the strongly recommended setup is to set the
ANTHROPIC_API_KEY environment variable on the Odoo host and leave the
ICP empty — the env var wins on resolution order. The ICP field is a
convenience for environments where setting env vars is impractical (e.g.
Odoo.sh staging where the admin only has UI access).
"""
import logging

from odoo import _, fields, models
from odoo.exceptions import UserError

from .ai_client import (
    SUPPORTED_MODELS,
    DEFAULT_MODEL,
    AIClient,
    AnthropicUnavailable,
)

_logger = logging.getLogger(__name__)


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    era_seo_ai_enabled = fields.Boolean(
        string='Enable AI Auto-Fix',
        config_parameter='era_seo.ai_enabled',
        help='Turn on Claude-powered "Suggest Fix" / "Apply Fix" actions on '
             'audit findings. Disabling is a hard switch — the buttons hide '
             'and no API calls happen.',
    )
    era_seo_ai_model = fields.Selection(
        SUPPORTED_MODELS,
        string='AI Model',
        default=DEFAULT_MODEL,
        config_parameter='era_seo.ai_model',
        help='Claude model used for suggestions. Haiku is fast and cheap and '
             'usually sufficient for SEO copy. Upgrade to Sonnet for nuanced '
             'multilingual work, or Opus for the highest-quality long-form '
             'output.',
    )
    era_seo_ai_api_key = fields.Char(
        string='Anthropic API Key',
        config_parameter='era_seo.ai_api_key',
        help='Optional. Strongly prefer setting the ANTHROPIC_API_KEY '
             'environment variable on the Odoo host instead; that takes '
             'precedence over any value set here. Keys saved here are '
             'visible to anyone with admin access to System Parameters.',
    )

    def action_test_ai_api_key(self):
        """Smoke-test the configured API key with a one-token round trip.

        Reads ICP and env so the admin can save the settings page first
        and click the button to verify before running a batch.
        """
        self.ensure_one()
        client = AIClient(self.env)
        ok, reason = client.is_available()
        if not ok:
            raise UserError(reason)

        try:
            anthropic = client._import_sdk()
            sdk_client = anthropic.Anthropic(
                api_key=client._api_key, max_retries=0,
            )
            response = sdk_client.messages.create(
                model=client._model,
                max_tokens=8,
                messages=[{'role': 'user', 'content': 'Reply with just "ok".'}],
            )
            text = next((b.text for b in response.content if b.type == 'text'), '')
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'type': 'success',
                    'message': _(
                        'API key works — Claude (%s) replied: %s',
                        client._model, (text or '').strip(),
                    ),
                    'sticky': False,
                    'next': {'type': 'ir.actions.act_window_close'},
                },
            }
        except AnthropicUnavailable as exc:
            raise UserError(str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise UserError(
                _('API call failed: %s', exc)
            ) from exc

"""Resilient AI generate_response endpoint (absorbed from era_odoo_ai_ext).

Avoids RPC 404 popups for stale channels and never crashes the endpoint; the
agent-level patch produces the user-facing message.
"""
import logging

from werkzeug.exceptions import NotFound

from odoo import http
from odoo.addons.ai.controllers.main import AIController as BaseAIController
from odoo.addons.mail.tools.discuss import add_guest_to_context

_logger = logging.getLogger(__name__)


class AIController(BaseAIController):
    @http.route(["/ai/generate_response"], type="jsonrpc", auth="public")
    @add_guest_to_context
    def generate_response(self, mail_message_id, channel_id, current_view_info=None, ai_session_identifier=None):
        channel = self._get_ai_channel_from_id(channel_id)
        if not channel:
            return {"ok": False, "reason": "channel_not_found"}
        try:
            message = self._get_message_with_access(mail_message_id)
        except NotFound:
            return {"ok": False, "reason": "message_not_found"}
        if not message:
            return {"ok": False, "reason": "message_not_found"}
        try:
            channel.sudo().ai_agent_id.with_context(
                current_view_info=current_view_info,
                ai_session_identifier=ai_session_identifier,
            )._generate_response_for_channel(message, channel)
        except Exception:  # noqa: BLE001
            _logger.exception("AI generate_response failed for channel %s", channel_id)
            return {"ok": False, "reason": "generation_failed"}
        return {"ok": True}

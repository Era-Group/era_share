import logging

from odoo import http, _
from odoo.http import request

_logger = logging.getLogger(__name__)


class WahaChatOverviewController(http.Controller):
    """Chat-list proxy. Reuses the base session's authenticated WAHA request
    helper so the x-api-key never reaches the browser."""

    @http.route('/sadeem_waha_chat/chats/overview', type='json', auth='user', methods=['POST'])
    def chats_overview(self, session_id=None, limit=20, offset=0):
        try:
            if not session_id:
                return {'success': False, 'error': 'Missing session id'}
            session = request.env['sadeem.waha.session'].sudo().browse(int(session_id))
            if not session.exists():
                return {'success': False, 'error': 'Session not found'}

            # GET /api/{session}/chats/overview?limit=&offset=
            endpoint = '%s/chats/overview' % session.session_id
            chats = session._make_api_request(
                endpoint, method='GET',
                params={'limit': limit, 'offset': offset},
            )

            if not isinstance(chats, list):
                chats = []

            # NOWEB/GOWS return empty unless the engine Store is enabled.
            if not chats and offset == 0 and session.engine in ('NOWEB', 'GOWS'):
                return {
                    'success': False,
                    'store_disabled': True,
                    'error': (
                        "No chats returned. For the %s engine you must enable the "
                        "Store to load chats and messages." % session.engine
                    ),
                }

            result = [self._map_chat(session, c) for c in chats]

            return {
                'success': True,
                # No total count from WAHA — caller stops when has_more is False.
                'has_more': len(chats) == int(limit),
                'chats': result,
            }
        except Exception as e:
            # _make_api_request raises with the upstream body, which on a
            # gateway error (e.g. Cloudflare 502) is a full HTML page. Log it
            # but never surface raw HTML to the UI.
            _logger.error("Error fetching chats overview: %s", e)
            return {
                'success': False,
                'error': _("Could not reach the WhatsApp server. Please try again shortly."),
            }

    def _map_chat(self, session, chat):
        """Shape one WAHA overview entry and attach the matching res.partner."""
        chat_id = chat.get('id') or ''
        partner = self._find_partner(chat_id)
        last = chat.get('lastMessage') or {}
        # WAHA NOWEB overview usually returns picture=null (profile pics need a
        # per-contact call, too slow for a list). Fall back to the matched
        # Odoo partner's avatar so known contacts still show a picture.
        picture = chat.get('picture') or ''
        if not picture and partner and partner.image_128:
            picture = '/web/image/res.partner/%s/avatar_128' % partner.id
        return {
            'id': chat_id,
            'name': chat.get('name') or '',
            'picture': picture,
            'unread': int(chat.get('unreadCount') or 0),
            'partner_id': partner.id if partner else False,
            'partner_name': partner.name if partner else (chat.get('name') or ''),
            'last_message': {
                'body': last.get('body') or '',
                'timestamp': last.get('timestamp') or 0,
                'fromMe': bool(last.get('fromMe')),
                'ack': last.get('ack'),
            },
        }

    def _find_partner(self, chat_id):
        """Look up res.partner from a WAHA chatId (e.g. 12345@c.us).
        Reuses the same phone_sanitized-first lookup the base module uses."""
        digits = (chat_id or '').split('@')[0]
        if not digits or not digits.isdigit():
            return request.env['res.partner']
        candidate = '+' + digits
        Partner = request.env['res.partner'].sudo()
        return Partner.search(
            ['|', ('phone_sanitized', '=', candidate), ('phone', '=', digits)],
            limit=1,
        )

    @http.route('/sadeem_waha_chat/react', type='json', auth='user', methods=['POST'])
    def react(self, session_id=None, chat_id=None, message_id=None, reaction=''):
        """React to a message (empty reaction string removes it).
        WAHA uses PUT /api/reaction {session, chatId, messageId, reaction}."""
        try:
            if not session_id or not message_id:
                return {'success': False, 'error': 'Missing session or message id'}
            session = request.env['sadeem.waha.session'].sudo().browse(int(session_id))
            if not session.exists():
                return {'success': False, 'error': 'Session not found'}
            session._make_api_request('reaction', method='PUT', data={
                'session': session.session_id,
                'chatId': chat_id,
                'messageId': message_id,
                'reaction': reaction or '',
            })
            return {'success': True}
        except Exception as e:
            _logger.error("Error sending reaction: %s", e)
            return {'success': False, 'error': _("Could not send the reaction.")}

    # ponytail: no WhatsApp profile-picture endpoint — WAHA NOWEB CORE tier
    # always returns profilePictureURL=null (Plus-tier feature). The chat list
    # falls back to the matched Odoo partner avatar in _map_chat; that's all the
    # picture data actually available. Revisit only if upgraded to WAHA Plus.

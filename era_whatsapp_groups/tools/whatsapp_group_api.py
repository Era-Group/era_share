# Part of Era Group custom addons.
"""Meta Cloud API Groups transport.

    ############################################################################
    #  UNVERIFIED AGAINST A LIVE ACCOUNT.
    #
    #  Every request shape below is transcribed from Meta's documentation and
    #  has NEVER been executed successfully, because the only number reachable
    #  from here is not Groups-eligible:
    #
    #      POST /v23.0/319651821233569/groups
    #        -> 400 (#131215) This phone number is not eligible to access
    #                          Groups APIs
    #
    #  What that error DOES prove, and the reason this module targets the
    #  upstream-pinned v23.0 rather than the v25.0 in Meta's group docs: the
    #  /groups edge EXISTS on v23.0. An absent edge answers with code 100
    #  ("Unsupported post request"); this answered with an eligibility check,
    #  and omitting `messaging_product` answered with a JSON-schema violation.
    #  Both replies come from a live handler, so routing is fine and only
    #  eligibility is missing.
    #
    #  THIS FILE IS THE ONLY PLACE THAT ENCODES META'S WIRE FORMAT. Everything
    #  else in the module -- routing, allowlisting, channel identity, WAHA
    #  coexistence -- is ours and is covered by tests. When the first live call
    #  succeeds and reality differs from the docs, correct it here and nowhere
    #  else.
    ############################################################################
"""
import json
import logging

from odoo.addons.whatsapp.tools.whatsapp_api import WhatsAppApi
from odoo.addons.whatsapp.tools.whatsapp_exception import WhatsAppError

_logger = logging.getLogger(__name__)

# Meta rejects a group with more participants than this. Enforced client-side so
# an over-full group fails with something readable instead of a raw 400 -- and so
# the limit is visible to anyone reading the code, since it is the single most
# consequential constraint on whether this module is useful at all.
WAG_MAX_PARTICIPANTS = 8


class WhatsAppGroupApi(WhatsAppApi):
    """Group endpoints, layered on the standard transport.

    Subclassed rather than patched: WhatsAppApi is a plain Python class, not an
    Odoo model, so `_inherit` does not reach it. Reusing it keeps the token,
    phone_uid, error translation and the `_check_allow_requests` test guard
    identical to upstream's.
    """

    def _wag_request(self, method, url, payload=None, params=False):
        """One chokepoint for every group call, so retries/logging stay in one place."""
        headers = {'Content-Type': 'application/json'} if payload is not None else None
        response = self._WhatsAppApi__api_requests(
            method, url, auth_type='bearer', headers=headers, params=params,
            data=json.dumps(payload) if payload is not None else False,
        )
        try:
            return response.json()
        except ValueError:
            raise WhatsAppError(failure_type='network')

    # -- discovery ---------------------------------------------------------

    def _wag_list_groups(self, limit=100, after=None):
        """GET /{phone_uid}/groups -> {'data': [...], 'paging': {...}}

        Reading is permitted on a number that cannot yet WRITE groups: the live
        probe returned 200 {"data": []} where the create returned 131215. So a
        clean empty sync is NOT proof of eligibility -- only a write is.
        """
        params = {'limit': limit}
        if after:
            params['after'] = after
        return self._wag_request('GET', f"/{self.phone_uid}/groups", params=params)

    def _wag_get_group(self, group_uid):
        return self._wag_request('GET', f"/{group_uid}")

    # -- management --------------------------------------------------------

    def _wag_create_group(self, subject, participants=None):
        participants = participants or []
        if len(participants) > WAG_MAX_PARTICIPANTS:
            raise WhatsAppError(
                failure_type='unknown',
                message=(
                    f"WhatsApp groups accept at most {WAG_MAX_PARTICIPANTS} participants; "
                    f"{len(participants)} were given."
                ),
            )
        payload = {'messaging_product': 'whatsapp', 'subject': subject}
        if participants:
            payload['participants'] = [{'user': p} for p in participants]
        return self._wag_request('POST', f"/{self.phone_uid}/groups", payload=payload)

    def _wag_update_group(self, group_uid, subject=None):
        payload = {'messaging_product': 'whatsapp'}
        if subject is not None:
            payload['subject'] = subject
        return self._wag_request('POST', f"/{group_uid}", payload=payload)

    def _wag_add_participants(self, group_uid, numbers):
        return self._wag_request(
            'POST', f"/{group_uid}/participants",
            payload={'messaging_product': 'whatsapp',
                     'participants': [{'user': n} for n in numbers]})

    def _wag_remove_participants(self, group_uid, numbers):
        return self._wag_request(
            'DELETE', f"/{group_uid}/participants",
            payload={'messaging_product': 'whatsapp',
                     'participants': [{'user': n} for n in numbers]})

    def _wag_invite_link(self, group_uid):
        return self._wag_request('GET', f"/{group_uid}/invite_link")

    # -- messaging ---------------------------------------------------------

    def _wag_send_to_group(self, group_uid, message_type, send_vals, parent_message_id=False):
        """POST /{phone_uid}/messages with recipient_type 'group'.

        Mirrors upstream `_send_whatsapp_to_identifier` (whatsapp_api.py:235-276)
        exactly, changing only the recipient discriminator -- so anything upstream
        learns about payload shape stays true here.

        UNVERIFIED: whether `context.message_id` (quoted reply) is honoured on a
        group send. It is passed through rather than silently dropped, because
        dropping it would quietly downgrade a reply into a loose message; if Meta
        rejects it, the error is explicit and this is the line to change.
        """
        data = {
            'messaging_product': 'whatsapp',
            'recipient_type': 'group',
            'to': group_uid,
        }
        if parent_message_id:
            data['context'] = {'message_id': parent_message_id}
        # Same whitelist as upstream: an unlisted type would otherwise produce a
        # payload with no content block that only Meta rejects, far from the cause.
        if message_type in ('template', 'text', 'document', 'image', 'audio', 'video', 'reaction'):
            data['type'] = message_type
            data[message_type] = send_vals
        else:
            raise WhatsAppError(
                failure_type='unknown',
                message=f"Unsupported message type for a group send: {message_type!r}")

        response = self._WhatsAppApi__api_requests(
            'POST', f"/{self.phone_uid}/messages", auth_type='bearer',
            headers={'Content-Type': 'application/json'}, data=json.dumps(data))
        response_json = response.json()
        if 'messages' not in response_json:
            raise WhatsAppError(*self._prepare_error_response(response_json))
        return {'msg_uid': response_json['messages'][0]['id']}

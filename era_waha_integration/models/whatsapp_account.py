# Part of Era Group custom addons.
import base64
import logging
import mimetypes
import re
import time
from datetime import datetime, timedelta, timezone

import psycopg2
import pytz
import requests
from markupsafe import Markup, escape

from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.addons.era_waha_integration.models.waha_exceptions import (
    WahaNewNumberLimit, WahaSendLimit)
from odoo.addons.mail.tools.discuss import Store
from odoo.exceptions import UserError, ValidationError
from odoo.modules.registry import Registry
from odoo.tools import plaintext2html

_logger = logging.getLogger(__name__)

# Public inbound webhook path (distinct from sadeem_waha_whatsapp's /waha/whatsapp/webhook)
WAHA_WEBHOOK_PATH = '/era/waha/webhook'
# Events we subscribe to on the WAHA session. We use 'message' (inbound only),
# NOT 'message.any', to avoid receiving echoes of our own outbound messages.
WAHA_WEBHOOK_EVENTS = ['message', 'message.ack', 'message.reaction', 'session.status']
# Retry failed webhook deliveries so a brief Odoo unavailability (a deploy restart, a
# blip) does NOT silently drop inbound messages: WAHA re-delivers until we answer, which
# keeps the live feed — and therefore message ordering — intact. Our webhook handler is
# idempotent (advisory lock + msg_uid dedup), so retries never duplicate. ~linear backoff.
WAHA_WEBHOOK_RETRIES = {'policy': 'linear', 'delaySeconds': 2, 'attempts': 15}
WAHA_REQUEST_TIMEOUT = 30
# A WhatsApp QR is only scannable briefly; past this it is stale and must not be shown.
WAHA_QR_TTL_SECONDS = 60
# Session statuses WAHA can legitimately report; anything else (e.g. a forged webhook)
# is ignored rather than written verbatim.
WAHA_KNOWN_STATUSES = frozenset({'starting', 'scan_qr_code', 'working', 'failed', 'stopped'})
# Human-like typing simulation before an outbound send (anti-ban pacing).
WAHA_TYPING_MIN_SECONDS = 2.0
WAHA_TYPING_MAX_SECONDS = 6.0
WAHA_TYPING_CHARS_PER_SECOND = 20.0
# Reconcile (gap catch-up after a disconnection): scan the most-recent chats from WAHA's
# chat overview and backfill any we're behind on, bounded to activity within this window
# so we never re-import ancient conversations.
WAHA_RECONCILE_WINDOW_HOURS = 72
WAHA_RECONCILE_MAX_CHATS = 300
# Per recently-active chat, inspect this many of the newest messages (not just the last
# one): a message missed during an outage often sits BELOW a later reply, so a
# last-message-only check would skip the chat and leave the gap.
WAHA_RECONCILE_TAIL = 20


class WhatsappAccount(models.Model):
    _inherit = 'whatsapp.account'

    provider = fields.Selection(
        selection=[('meta', 'Meta Cloud API'), ('waha', 'WAHA (WhatsApp Web)')],
        string='Provider', default='meta', required=True, tracking=True,
        help="Backend used to send/receive messages for this account.")

    # WAHA connection
    waha_server_url = fields.Char(string='WAHA Server URL', default='http://localhost:3000')
    waha_api_key = fields.Char(string='WAHA API Key', groups='whatsapp.group_whatsapp_admin')
    waha_session = fields.Char(
        string='WAHA Session', help="WAHA session name. Must be unique and dedicated to this "
        "integration (do not share a session with another module).")
    waha_engine = fields.Selection(
        [('NOWEB', 'NOWEB'), ('WEBJS', 'WEBJS'), ('GOWS', 'GOWS')],
        string='WAHA Engine', default='NOWEB')
    waha_status = fields.Char(string='WAHA Status', readonly=True, copy=False)
    # attachment-backed (not stored inline on the row) so QR writes don't contend with
    # concurrent waha_status writes on the same whatsapp_account row.
    waha_qr_image = fields.Binary(string='WAHA QR Code', readonly=True, copy=False)
    waha_qr_fetched = fields.Datetime(string='QR fetched at', readonly=True, copy=False)
    waha_qr_valid = fields.Boolean(compute='_compute_waha_qr_valid',
                                   help="The QR is shown only while it is scannable: the session is "
                                        "waiting to be linked and the code was fetched recently.")
    waha_webhook_secret = fields.Char(
        string='Webhook HMAC Secret', groups='whatsapp.group_whatsapp_admin',
        help="Optional shared secret. When set, WAHA signs webhooks with this key "
             "(X-Webhook-Hmac, sha512) and the controller verifies the signature.")
    waha_max_media_mb = fields.Integer(
        string='Max attachment (MB)', default=16,
        help="Reject outbound attachments whose file size exceeds this, with a clear error, "
             "before they hit the WAHA host's reverse-proxy body limit (a raw 413). Because "
             "base64 inflates the HTTP body ~33%, the WAHA host's nginx client_max_body_size "
             "must be at least ~1.33x this value (e.g. cap 16 -> nginx >= 22m). 0 = no check.")

    # History import configuration
    waha_auto_import_history = fields.Boolean(
        string='Auto-import history on new channel', default=True,
        help="When a channel is first created from an inbound message, import the recent "
             "conversation history so the operator has context.")
    waha_history_limit = fields.Integer(string='History import limit', default=50)
    waha_reconcile_on_reconnect = fields.Boolean(
        string='Reconcile on reconnect', default=True,
        help="When the session comes back to 'working' after a disconnection, "
              "check active channels and backfill any missed messages.")
    waha_group_ids = fields.One2many('whatsapp.waha.group', 'account_id', string='Groups')

    # ---- Account-protection (anti-ban) limits ----
    waha_working_since = fields.Datetime(
        string='Working Since', readonly=True, copy=False,
        help="First time the session became 'working'; used to age the sending balance.")
    waha_new_number_daily_limit = fields.Integer(
        string='New numbers / user / day', default=5,
        help="Max brand-new numbers each user may first-contact per day on this "
             "(unofficial) account. Beyond it, they are redirected to official WhatsApp. 0 = no limit.")
    waha_cold_resend_hours = fields.Integer(
        string='Cold re-send lock (hours)', default=24,
        help="A contact that never replied can only be messaged once per this many hours.")
    waha_balance_ratio = fields.Float(
        string='Send/receive ratio', default=1.0,
        help="Max outbound per inbound over the balance window (grows automatically with account age).")
    waha_balance_base = fields.Integer(
        string='Balance base allowance', default=25,
        help="Outbound messages allowed regardless of inbound (covers seed outreach).")
    waha_balance_window_hours = fields.Integer(string='Balance window (hours)', default=24)
    waha_balance_ratio_max = fields.Float(string='Max ratio', default=4.0)
    waha_balance_ratio_step = fields.Float(
        string='Ratio step / week', default=0.5,
        help="Weekly automatic increase of the send/receive ratio as the account ages.")

    # ---- Account health monitoring ----
    waha_flap_count = fields.Integer(string='Status flaps (today)', default=0, copy=False)
    waha_health_alerted = fields.Boolean(default=False, copy=False)
    waha_health_alert_date = fields.Date(copy=False)
    waha_health_score = fields.Integer(string='Health Score', compute='_compute_waha_health')
    waha_health_label = fields.Selection(
        [('good', 'Good'), ('warning', 'Warning'), ('critical', 'Critical')],
        string='Health', compute='_compute_waha_health')
    waha_kpi_outbound = fields.Integer(string='Outbound (7d)', compute='_compute_waha_health')
    waha_kpi_inbound = fields.Integer(string='Inbound (7d)', compute='_compute_waha_health')
    waha_kpi_delivered_rate = fields.Float(string='Delivered %', compute='_compute_waha_health')
    waha_kpi_read_rate = fields.Float(string='Read %', compute='_compute_waha_health')
    waha_kpi_error_rate = fields.Float(string='Error %', compute='_compute_waha_health')
    waha_health_reason = fields.Html(string='Why this score', compute='_compute_waha_health', sanitize=False)

    # ---- Health scoring tuning (delivery penalty) ----
    # WAHA's unofficial delivery receipts are unreliable, so these default lenient.
    # A tier is disabled by setting its threshold OR its penalty to 0.
    waha_health_deliv_warn_pct = fields.Integer(
        string='Delivery warn below (%)', default=50,
        help="If confirmed-delivery % over the last 7 days is below this, subtract the warn "
             "penalty from the health score. Kept low by default because WAHA delivery receipts "
             "are often delayed. Set 0 to disable this tier.")
    waha_health_deliv_warn_penalty = fields.Integer(
        string='Delivery warn penalty (points)', default=5,
        help="Points removed from the health score when delivery is below the warn threshold.")
    waha_health_deliv_crit_pct = fields.Integer(
        string='Delivery critical below (%)', default=30,
        help="If confirmed-delivery % is below this, the larger critical penalty applies instead "
             "of the warn one. Set 0 to disable this tier.")
    waha_health_deliv_crit_penalty = fields.Integer(
        string='Delivery critical penalty (points)', default=15,
        help="Points removed from the health score when delivery is below the critical threshold.")

    # Relax Meta-required fields so WAHA accounts can be saved without Meta credentials.
    # Odoo merges field attributes across _inherit, so groups/strings are preserved.
    app_uid = fields.Char(required=False)
    app_secret = fields.Char(required=False)
    account_uid = fields.Char(required=False)
    phone_uid = fields.Char(required=False)
    token = fields.Char(required=False)

    # ------------------------------------------------------------------
    # Constraints / computes
    # ------------------------------------------------------------------
    @api.constrains('provider', 'app_uid', 'account_uid', 'phone_uid', 'token',
                    'waha_server_url', 'waha_session')
    def _check_provider_fields(self):
        for account in self:
            acc = account.sudo()  # token/app_secret are group-restricted
            if acc.provider == 'meta':
                missing = [f for f in ('app_uid', 'account_uid', 'phone_uid', 'token') if not acc[f]]
                if missing:
                    raise ValidationError(_(
                        "Meta accounts require: %(fields)s", fields=', '.join(missing)))
            elif acc.provider == 'waha':
                if not acc.waha_server_url or not acc.waha_session:
                    raise ValidationError(_("WAHA accounts require a server URL and a session name."))

    def _compute_callback_url(self):
        waha = self.filtered(lambda a: a.provider == 'waha')
        for account in waha:
            account.callback_url = account.get_base_url() + WAHA_WEBHOOK_PATH
        super(WhatsappAccount, self - waha)._compute_callback_url()

    # ------------------------------------------------------------------
    # Per-account member access
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        accounts = super().create(vals_list)
        for account in accounts.filtered(lambda a: a.provider == 'waha' and a.notify_user_ids):
            account._waha_apply_channel_members(account.notify_user_ids, account.env['res.users'])
        return accounts

    def write(self, vals):
        old_users = {}
        if 'notify_user_ids' in vals:
            old_users = {a.id: a.notify_user_ids for a in self}
        res = super().write(vals)
        if 'notify_user_ids' in vals or 'provider' in vals:
            for account in self.filtered(lambda a: a.provider == 'waha'):
                previous = old_users.get(account.id, account.notify_user_ids)
                account._waha_apply_channel_members(
                    account.notify_user_ids - previous, previous - account.notify_user_ids)
        return res

    def _waha_apply_channel_members(self, added_users, removed_users):
        """Add/remove the given users on all of this account's channels so the account's
        users see (and can post in) every conversation of the account."""
        self.ensure_one()
        channels = self.env['discuss.channel'].sudo().search([('wa_account_id', '=', self.id)])
        if not channels:
            return
        if added_users:
            channels.add_members(partner_ids=added_users.partner_id.ids, post_joined_message=False)
        if removed_users:
            self.env['discuss.channel.member'].sudo().search([
                ('channel_id', 'in', channels.ids),
                ('partner_id', 'in', removed_users.partner_id.ids),
            ]).unlink()
        self._waha_keep_channel_members_pinned()

    def _waha_keep_channel_members_pinned(self):
        """WAHA is a shared inbox: retain internal conversation history in Discuss."""
        self.ensure_one()
        members = self.env['discuss.channel.member'].sudo().search([
            ('channel_id.wa_account_id', '=', self.id),
            ('partner_id.user_ids.share', '=', False),
        ])
        members.filtered('unpin_dt').write({'unpin_dt': False})
        # Re-send the channel header to each member. Without this, an already-open
        # browser keeps its old unpinned sidebar cache until a full reload.
        for member in members:
            member.channel_id._broadcast(member.partner_id.ids)

    def _waha_backfill_notification_participants(self):
        """Restore participants from historical replies without issuing alerts."""
        self.ensure_one()
        channels = self.env['discuss.channel'].sudo().search([
            ('wa_account_id', '=', self.id),
            ('channel_type', '=', 'whatsapp'),
        ])
        for channel in channels:
            users = channel._waha_backfill_participants()
            if not users:
                continue
            latest_message = self.env['mail.message'].sudo().search([
                ('model', '=', 'discuss.channel'),
                ('res_id', '=', channel.id),
            ], order='id desc', limit=1)
            if latest_message:
                channel._waha_mark_nonrecipients_as_read(
                    latest_message, users.partner_id.ids, notify=False)

    def _waha_grant_members(self, channel):
        """Add this account's users (notify_user_ids) to a freshly-created channel."""
        self.ensure_one()
        if channel and self.notify_user_ids:
            channel.sudo().add_members(
                partner_ids=self.notify_user_ids.partner_id.ids, post_joined_message=False)
            self._waha_keep_channel_members_pinned()

    # ------------------------------------------------------------------
    # WAHA HTTP helpers
    # ------------------------------------------------------------------
    def _waha_headers(self):
        headers = {'Content-Type': 'application/json'}
        # waha_api_key is restricted to the WhatsApp admin group; read it via sudo so a
        # non-admin account Member can still authenticate WAHA calls when sending. The key
        # only reaches the WAHA server (an HTTP header), never the user.
        api_key = self.sudo().waha_api_key
        if api_key:
            headers['X-Api-Key'] = api_key
        return headers

    def _waha_http(self, endpoint, method='GET', data=None, params=None, timeout=None):
        base = (self.waha_server_url or '').rstrip('/')
        url = f"{base}/api/{endpoint.lstrip('/')}"
        return requests.request(
            method, url,
            json=data if method in ('POST', 'PUT') else None,
            params=params, headers=self._waha_headers(), timeout=timeout or WAHA_REQUEST_TIMEOUT)

    def _waha_request(self, endpoint, method='GET', data=None, params=None, timeout=None):
        """Perform a WAHA API call, raising UserError on error, returning parsed JSON or bytes."""
        self.ensure_one()
        try:
            resp = self._waha_http(endpoint, method, data, params, timeout=timeout)
        except requests.exceptions.RequestException as err:
            raise UserError(_("Failed to reach WAHA server: %s") % err)
        if resp.status_code not in (200, 201):
            raise UserError(_("WAHA API error %(code)s: %(body)s") % {
                'code': resp.status_code, 'body': self._waha_error_body(resp)})
        if resp.headers.get('Content-Type', '').startswith('application/json'):
            return resp.json()
        return resp.content

    @api.model
    def _waha_error_body(self, resp):
        """Best-effort readable body of a failed WAHA response (JSON if possible, else text)."""
        try:
            return resp.json()
        except Exception:
            return resp.text

    @api.model
    def _waha_extract_id(self, raw):
        """Normalize a WAHA message id from the various shapes engines return.

        Accepts a plain serialized string, a dict with ``_serialized``, a dict
        whose ``id`` is itself a string or dict, a NOWEB/Baileys ``key`` wrapper
        (``{'key': {'id': ...}}``), or a whole send/webhook response object — and
        digs out the serialized id string. Returns False if none is found.
        """
        if not raw:
            return False
        if isinstance(raw, str):
            return raw
        if isinstance(raw, dict):
            serialized = raw.get('_serialized')
            if isinstance(serialized, str) and serialized:
                return serialized
            for key in ('id', 'key', 'messageId', 'message_id'):
                value = raw.get(key)
                if value and value is not raw:
                    found = self._waha_extract_id(value)
                    if found:
                        return found
        return False

    def _waha_chat_id(self, number):
        digits = re.sub(r'\D', '', number or '')
        return f"{digits}@c.us" if digits else ''

    @api.model
    def _waha_group_chat_id(self, payload):
        data = payload.get('_data') or {}
        candidates = [
            payload.get('chatId'), payload.get('from'),
            data.get('chatId'), data.get('remoteJid'),
            (data.get('key') or {}).get('remoteJid'),
        ]
        return next((value for value in candidates if isinstance(value, str) and value.endswith('@g.us')), '')

    @api.model
    def _waha_participant_jid(self, payload):
        data = payload.get('_data') or {}
        key = data.get('key') or {}
        candidates = [
            payload.get('participant'), data.get('participant'), data.get('author'),
            key.get('participant'), key.get('remoteJidAlt'), data.get('remoteJidAlt'),
        ]
        return next((value for value in candidates if isinstance(value, str) and value), '')

    def _waha_resolve_participant(self, participant, payload, sender_name=False):
        """Return a matching/created sender partner or False for unresolved LID identities."""
        number = self._waha_resolve_number(participant, payload)
        if not number:
            return self.env['res.partner']
        identifiers = {'number': number}
        partner = self.env['res.partner']._find_or_create_from_whatsapp_identifiers(
            identifiers, sender_name or number, self)
        if sender_name and partner.name in (number, '+' + number, partner.phone):
            partner.name = sender_name
        return partner

    @api.model
    def _waha_group_sender_prefix(self, content, sender_name, participant):
        label = sender_name or (participant[-8:] if participant else _('Unknown sender'))
        prefix = Markup('<p><strong>{}</strong></p>').format(escape(label))
        content['body'] = prefix + Markup(content.get('body') or '')
        return content

    def action_waha_sync_groups(self):
        self.ensure_one()
        if self.provider != 'waha':
            raise UserError(_('This action is only available on WAHA accounts.'))
        Group = self.env['whatsapp.waha.group'].sudo()
        seen, offset, page = set(), 0, 100
        while True:
            data = self._waha_request(
                f'{self.waha_session}/groups', 'GET',
                params={'limit': page, 'offset': offset, 'exclude': 'participants'})
            # WAHA engines differ here: most return a list (or ``{'groups': [...]}``),
            # while NOWEB may return ``{'123@g.us': {...}, ...}``.
            if isinstance(data, dict) and isinstance(data.get('groups'), list):
                groups = data['groups']
            elif isinstance(data, dict):
                groups = [value | {'id': key} if isinstance(value, dict) else {'id': key}
                          for key, value in data.items()
                          if isinstance(key, str) and key.endswith('@g.us')]
            else:
                groups = data
            groups = groups if isinstance(groups, list) else []
            for raw in groups:
                raw = raw or {}
                chat_id = raw.get('id') or raw.get('chatId') or (raw.get('_data') or {}).get('id')
                if isinstance(chat_id, dict):
                    chat_id = chat_id.get('_serialized') or chat_id.get('id')
                if not isinstance(chat_id, str) or not chat_id.endswith('@g.us'):
                    continue
                subject = raw.get('subject') or raw.get('name') or raw.get('title') or (raw.get('_data') or {}).get('subject')
                seen.add(chat_id)
                group = Group.search([('account_id', '=', self.id), ('chat_id', '=', chat_id)], limit=1)
                vals = {'subject': subject or chat_id, 'available': True, 'last_sync_at': fields.Datetime.now()}
                if group:
                    group.write(vals)
                else:
                    group = Group.create(vals | {'account_id': self.id, 'chat_id': chat_id, 'enabled': False})
                channels = self.env['discuss.channel'].sudo().search([
                    ('wa_account_id', '=', self.id), ('waha_chat_id', '=', chat_id)])
                if channels and subject:
                    channels.write({'name': subject, 'waha_group_id': group.id})
            if len(groups) < page:
                break
            offset += page
        missing = Group.search([('account_id', '=', self.id), ('chat_id', 'not in', list(seen))])
        missing.write({'available': False, 'last_sync_at': fields.Datetime.now()})
        return True

    @api.model
    def _waha_sanitize(self, raw):
        if not raw:
            return ''
        for suffix in ('@c.us', '@s.whatsapp.net', '@lid', '@g.us', '@broadcast'):
            raw = raw.replace(suffix, '')
        raw = raw.strip()
        if raw.startswith('+'):
            digits = '+' + ''.join(c for c in raw[1:] if c.isdigit())
        else:
            digits = ''.join(c for c in raw if c.isdigit())
        count = len(digits.lstrip('+'))
        if count < 7 or count > 15:
            return ''
        return digits

    # ------------------------------------------------------------------
    # Session / QR management
    # ------------------------------------------------------------------
    def _waha_webhook_config(self):
        webhook = {'url': self.get_base_url() + WAHA_WEBHOOK_PATH, 'events': WAHA_WEBHOOK_EVENTS,
                   'retries': WAHA_WEBHOOK_RETRIES}
        if self.waha_webhook_secret:
            webhook['hmac'] = {'key': self.waha_webhook_secret}
        return {
            'noweb': {'store': {'enabled': True, 'full_sync': True}},
            'webhooks': [webhook],
        }

    def action_waha_start_session(self):
        self.ensure_one()
        config = self._waha_webhook_config()
        payload = {'name': self.waha_session, 'start': True, 'config': config}
        try:
            resp = self._waha_http('sessions', 'POST', data=payload)
        except requests.exceptions.RequestException as err:
            raise UserError(_("Failed to reach WAHA server: %s") % err)
        if resp.status_code == 422:
            # Session already exists: update its config (webhooks) without restarting.
            self._waha_request(f'sessions/{self.waha_session}', 'PUT', data={
                'name': self.waha_session, 'config': config})
        elif resp.status_code not in (200, 201):
            raise UserError(_("WAHA start session failed %(code)s: %(body)s") % {
                'code': resp.status_code, 'body': self._waha_error_body(resp)})
        self._waha_write_status('starting')
        return self.action_waha_get_qr()

    def action_waha_stop_session(self):
        self.ensure_one()
        self._waha_request(f'sessions/{self.waha_session}', 'DELETE')
        self._waha_write_status('stopped')
        self._waha_update({'waha_qr_image': False, 'waha_qr_fetched': False})
        return self._waha_status_notification()

    def action_waha_get_qr(self):
        self.ensure_one()
        try:
            screenshot = self._waha_request('screenshot', 'GET', params={'session': self.waha_session})
            if isinstance(screenshot, bytes):
                encoded = base64.b64encode(screenshot)
                # Always stamp the fetch time so freshness is tracked even when the image
                # bytes are identical to the previous poll.
                self._waha_update({'waha_qr_image': encoded, 'waha_qr_fetched': fields.Datetime.now()})
        except UserError:
            # QR unavailable (probably already authenticated)
            pass
        # NOTE: we deliberately do NOT refresh status here. Writing waha_status on every
        # QR poll races with the concurrent session.status webhook writer on the same row
        # and produces "could not serialize access due to concurrent update" errors.
        # Status arrives via the webhook and the periodic/manual refresh instead.
        return None

    def _waha_probe_status(self):
        """Fetch the live session status from WAHA, persist it, and RETURN it.

        Returns the fetched status string (lowercased), or None if the probe
        itself could not be performed (network/API error). Callers that must
        decide within the SAME transaction have to branch on this return value,
        NOT on self.waha_status: the persist runs through _waha_update on an
        autonomous cursor, and the caller's REPEATABLE READ snapshot (fixed
        before that commit) cannot see the new value — a re-read would return
        the stale pre-probe value. The autonomous write is only for persisting
        the status to *future* transactions.
        """
        self.ensure_one()
        if self.provider != 'waha' or not self.waha_session:
            return None
        try:
            data = self._waha_request(f'sessions/{self.waha_session}')
        except UserError as err:
            _logger.warning("WAHA status probe failed for %s: %s", self.waha_session, err)
            self._waha_write_status('failed')
            return None
        if isinstance(data, dict):
            self._waha_apply_status(data.get('status'), data.get('me') or {})
            return (data.get('status') or '').lower()
        return None

    def action_waha_refresh_status(self):
        for account in self.filtered(lambda a: a.provider == 'waha' and a.waha_session):
            account._waha_probe_status()
        return None

    def button_test_connection(self):
        if self.provider == 'waha':
            self.ensure_one()
            # Use the probe's returned status for the toast: the autonomous persist
            # is invisible to this request's snapshot, so self.waha_status would be stale.
            live = self._waha_probe_status()
            return self._waha_status_notification(status=live)
        return super().button_test_connection()

    def _waha_update(self, vals):
        """Write volatile session fields (waha_status/phone_number/waha_qr_image) in a
        short *autonomous* transaction, swallowing concurrent-update failures.

        These fields change rapidly while a session connects (starting/scan_qr_code
        toggling) and are written from several places at once — the session.status
        webhook, the QR/refresh requests and the status cron. Odoo requests run in
        REPEATABLE READ, so concurrent writes to the same whatsapp_account row abort
        the whole request with "could not serialize access due to concurrent update"
        and exhaust its retry loop. Persisting these out-of-band keeps the request
        itself from ever failing; a lost write is corrected by the next event/refresh.
        """
        self.ensure_one()
        if not vals:
            return False
        try:
            with Registry(self.env.cr.dbname).cursor() as cr:
                api.Environment(cr, SUPERUSER_ID, {})['whatsapp.account'].browse(self.id).write(vals)
                cr.commit()
        except psycopg2.Error as err:
            _logger.debug("WAHA volatile write skipped for account %s: %s", self.id, err)
            return False
        self.invalidate_recordset(list(vals))
        return True

    def _waha_write_status(self, status, me=None):
        """Change-guarded, out-of-band write of the session status (+ phone)."""
        self.ensure_one()
        status = (status or '').lower()
        # Ignore empty or unrecognized statuses (defensive against forged webhooks).
        if status not in WAHA_KNOWN_STATUSES:
            return False
        vals = {}
        if status != self.waha_status:
            vals['waha_status'] = status
            vals['waha_flap_count'] = (self.waha_flap_count or 0) + 1  # health: status volatility
        if status == 'working' and not self.waha_working_since:
            vals['waha_working_since'] = fields.Datetime.now()
        # Once the session leaves the scan state (linked, stopped or failed) any stored QR
        # is stale/consumed — drop it so the form never shows an invalid code.
        if status not in ('starting', 'scan_qr_code') and (self.waha_qr_image or self.waha_qr_fetched):
            vals['waha_qr_image'] = False
            vals['waha_qr_fetched'] = False
        if me and me.get('id'):
            # Keep digits only — never store an arbitrary webhook-supplied string as the phone.
            new_phone = re.sub(r'\D', '', me['id'].split('@')[0])
            if new_phone and new_phone != self.phone_number:
                vals['phone_number'] = new_phone
        return self._waha_update(vals)

    def _waha_apply_status(self, status, me):
        self.ensure_one()
        was_working = self.waha_status == 'working'
        self._waha_write_status(status, me)
        if not was_working and (status or '').lower() == 'working' and self.waha_reconcile_on_reconnect:
            # Defer the (heavy) history reconcile to its own transaction so it neither
            # lengthens this request nor adds to row contention.
            self.env.ref('era_waha_integration.ir_cron_waha_reconcile')._trigger()

    @api.depends('waha_qr_image', 'waha_status', 'waha_qr_fetched')
    def _compute_waha_qr_valid(self):
        now = fields.Datetime.now()
        for account in self:
            fresh = bool(account.waha_qr_fetched) and \
                (now - account.waha_qr_fetched).total_seconds() <= WAHA_QR_TTL_SECONDS
            account.waha_qr_valid = bool(
                account.provider == 'waha' and account.waha_qr_image
                and account.waha_status in ('starting', 'scan_qr_code') and fresh)

    def _waha_status_notification(self, status=None):
        status = status if status is not None else self.waha_status
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'type': 'success' if status == 'working' else 'warning',
                'message': _("WAHA session status: %s", status or 'unknown'),
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }

    # ------------------------------------------------------------------
    # Outbound (used by whatsapp.message._waha_send_one)
    # ------------------------------------------------------------------
    def _waha_send_text(self, chat_id, text):
        self.ensure_one()
        return self._waha_request('sendText', 'POST', data={
            'chatId': chat_id, 'text': text or '', 'session': self.waha_session})

    def _waha_send_media(self, chat_id, attachment, caption=''):
        self.ensure_one()
        mimetype = attachment.mimetype or 'application/octet-stream'
        data_b64 = base64.b64encode(attachment.raw or b'').decode()
        file_obj = {'mimetype': mimetype, 'filename': attachment.name or 'file', 'data': data_b64}
        payload = {'chatId': chat_id, 'session': self.waha_session, 'file': file_obj}
        if mimetype.startswith('audio/'):
            endpoint = 'sendVoice'
        else:
            endpoint = 'sendImage' if mimetype.startswith('image/') else 'sendFile'
            if caption:
                payload['caption'] = caption
        return self._waha_request(endpoint, 'POST', data=payload)

    def _waha_react(self, wa_message, emoji):
        self.ensure_one()
        chat_id = wa_message.waha_chat_id or self._waha_chat_id(
            wa_message.mobile_number or wa_message.mobile_number_formatted or '')
        return self._waha_request('reaction', 'PUT', data={
            'session': self.waha_session, 'chatId': chat_id,
            'messageId': wa_message.msg_uid, 'reaction': emoji or ''}, timeout=8)

    def _waha_simulate_typing(self, chat_id, text):
        """Show a 'typing…' presence to the recipient for a human-like, length-scaled
        duration (min 2s, capped) before the message is sent — a pacing signal that makes
        the (unofficial) account look less bot-like. Best-effort: presence errors never
        block the send."""
        self.ensure_one()
        if not chat_id:
            return
        delay = min(WAHA_TYPING_MAX_SECONDS,
                    max(WAHA_TYPING_MIN_SECONDS, len(text or '') / WAHA_TYPING_CHARS_PER_SECOND))
        acc = self.sudo()
        try:
            acc._waha_request('startTyping', 'POST',
                              data={'session': self.waha_session, 'chatId': chat_id}, timeout=8)
        except Exception:
            _logger.debug("WAHA startTyping failed for %s", chat_id)
        time.sleep(delay)
        try:
            acc._waha_request('stopTyping', 'POST',
                              data={'session': self.waha_session, 'chatId': chat_id}, timeout=8)
        except Exception:
            _logger.debug("WAHA stopTyping failed for %s", chat_id)

    def _waha_mark_seen(self, channel):
        """Send a read receipt to WhatsApp so the customer sees blue (read) ticks once an
        agent reads the chat in Odoo. Short timeout + sudo so it never blocks the agent's
        UI and works for non-admin users."""
        self.ensure_one()
        if self.provider != 'waha':
            return
        chat_id = channel.waha_chat_id or self._waha_chat_id(channel.whatsapp_number or '')
        if not chat_id:
            return
        self.sudo()._waha_request(
            'sendSeen', 'POST',
            data={'session': self.waha_session, 'chatId': chat_id}, timeout=8)

    def _waha_send_via_channel(self, number, body, attachment_ids=None, sender_name=False, author=None):
        """Send an outbound WAHA message by routing it through the recipient's Discuss
        channel (found/created from the number): it shows in the conversation and is
        delivered via WAHA. Shared by the WhatsApp and mail composers. Returns the
        channel (empty recordset if the number could not be resolved)."""
        self.ensure_one()
        channel = self.env['discuss.channel'].sudo()._get_whatsapp_channel_from_identifiers(
            self, {'number': number}, sender_name=sender_name, create_if_not_found=True)
        if not channel:
            return channel
        self._waha_grant_members(channel)
        # message_type='whatsapp_message' drives the outbound WAHA send + Discuss display.
        channel.message_post(
            body=body or '',
            message_type='whatsapp_message',
            author_id=(author or self.env.user.partner_id).id,
            subtype_xmlid='mail.mt_comment',
            attachment_ids=attachment_ids or [],
        )
        return channel

    # ------------------------------------------------------------------
    # Unanswered inbound-message escalation
    # ------------------------------------------------------------------
    def _waha_company_is_working(self, company, now):
        """Whether the company default calendar is open at ``now`` (UTC-naive)."""
        calendar = company.resource_calendar_id
        if not calendar:
            # A missing calendar must not silently strand customer messages forever.
            return True
        now_aware = now.replace(tzinfo=timezone.utc)
        intervals = calendar._work_intervals_batch(
            now_aware,
            now_aware + timedelta(minutes=1),
            domain=[('company_id', 'in', [False, company.id])],
        )
        return bool(intervals.get(False))

    def _cron_waha_escalate_unanswered(self):
        """Escalate live inbound messages still unanswered after one working hour.

        A row lock and an escalation timestamp make this safe when multiple cron
        workers run at once. The timestamp is written before dispatch so a browser
        push retry cannot turn into repeated team-wide interruptions.
        """
        now = fields.Datetime.now()
        due_before = now - timedelta(hours=1)
        messages = self.env['mail.message'].sudo().search([
            ('model', '=', 'discuss.channel'),
            ('waha_pending_reply', '=', True),
            ('waha_escalated_at', '=', False),
            ('create_date', '<=', due_before),
        ], order='create_date', limit=200)
        for message in messages:
            try:
                with self.env.cr.savepoint():
                    self.env.cr.execute(
                        'SELECT id FROM mail_message WHERE id = %s FOR UPDATE SKIP LOCKED',
                        [message.id],
                    )
                    if not self.env.cr.fetchone():
                        continue
                    message.invalidate_recordset()
                    if not message.waha_pending_reply or message.waha_escalated_at:
                        continue
                    channel = self.env['discuss.channel'].sudo().browse(message.res_id).exists()
                    if (not channel or channel.channel_type != 'whatsapp'
                            or channel.wa_account_id.provider != 'waha'):
                        continue
                    account = channel.wa_account_id
                    # WhatsApp accounts are multi-company through
                    # ``allowed_company_ids`` rather than a single company_id.
                    company = account.allowed_company_ids[:1] or self.env.company
                    if not self._waha_company_is_working(company, now):
                        continue
                    users = account.notify_user_ids.filtered(
                        lambda user: user.active and not user.share)
                    if not users:
                        _logger.warning(
                            'WAHA: no Default Users to escalate unanswered message %s for account %s',
                            message.id, account.id)
                        message.write({'waha_escalated_at': now})
                        continue
                    recipients_data = channel._waha_recipients_for_users(message, users)
                    message.write({'waha_escalated_at': now})
                    if recipients_data:
                        channel._notify_thread_by_web_push(message, recipients_data)
            except Exception:
                _logger.exception('WAHA: failed escalating unanswered message %s', message.id)

    # ------------------------------------------------------------------
    # Inbound processing (called from the webhook controller)
    # ------------------------------------------------------------------
    def _waha_resolve_number(self, from_contact, payload):
        if '@lid' in (from_contact or ''):
            try:
                encoded = from_contact.replace('@', '%40')
                data = self._waha_request(f'{self.waha_session}/lids/{encoded}', 'GET')
                pn = data.get('pn') if isinstance(data, dict) else None
                if pn:
                    return self._waha_sanitize(pn)
            except Exception:
                _logger.debug("WAHA LID resolution failed for %s", from_contact)
            alt = (payload.get('_data') or {}).get('key', {}).get('remoteJidAlt')
            if alt:
                return self._waha_sanitize(alt)
            return ''
        return self._waha_sanitize(from_contact)

    def _waha_download_media(self, payload):
        media = payload.get('media') or {}
        url = media.get('url')
        mimetype = media.get('mimetype') or 'application/octet-stream'
        filename = media.get('filename')
        content = None
        if url:
            rewritten = re.sub(r'^https?://[^/]+', (self.waha_server_url or '').rstrip('/'), url)
            try:
                resp = requests.get(rewritten, headers=self._waha_headers(), timeout=WAHA_REQUEST_TIMEOUT)
                if resp.status_code == 200 and not resp.headers.get('Content-Type', '').startswith('text/html'):
                    content = resp.content
            except requests.exceptions.RequestException:
                content = None
        if not content:
            b64 = (payload.get('_data') or {}).get('body')
            if b64:
                try:
                    content = base64.b64decode(b64)
                except Exception:
                    content = None
        if not content:
            return None
        if not filename:
            ext = mimetypes.guess_extension(mimetype) or ''
            filename = f"media_{self._waha_extract_id(payload.get('id')) or 'file'}{ext}"
        ptype = payload.get('type') or (payload.get('_data') or {}).get('type') or ''
        is_voice = ptype in ('ptt', 'voice') or mimetype.startswith('audio/')
        return (filename, content, {'voice': is_voice})

    def _waha_build_content(self, payload):
        """Return message_post kwargs (body / attachments) for a WAHA message payload."""
        content = {}
        mtype = (payload.get('type') or (payload.get('_data') or {}).get('type')
                 or ('image' if payload.get('hasMedia') else 'text'))
        caption = payload.get('body') or ''
        if mtype in ('image', 'video', 'document', 'audio', 'ptt', 'voice') or payload.get('hasMedia'):
            attachment = self._waha_download_media(payload)
            if attachment:
                content['attachments'] = [attachment]
        if caption:
            content['body'] = plaintext2html(caption)
        # Stamp the message with WAHA's own timestamp (not "now") so live AND imported
        # messages sort chronologically — a message delivered late (e.g. a webhook retry
        # after downtime, or a gap backfill) keeps its real send time instead of jumping to
        # the bottom. This is what the WAHA-channel date ordering (thread_patch.js) relies on.
        ts = payload.get('timestamp')
        if ts:
            try:
                content['date'] = fields.Datetime.to_string(
                    datetime.fromtimestamp(int(ts), tz=timezone.utc).replace(tzinfo=None))
            except (ValueError, TypeError, OSError):
                pass
        return content

    def _waha_uid_exists(self, uid):
        """Single source of truth for msg_uid idempotency: True if a whatsapp.message with
        this uid already exists. Used by the webhook dedup lock and the history backfill.

        Matches the full serialized id OR its trailing hash segment, because the same
        message is stored under different id shapes: live outbound as the bare send-response
        HASH, inbound under its `@lid` serialized id, while the chat/messages and overview
        APIs return the full `fromMe_remoteJid_HASH` (often with a `@c.us` remoteJid). The
        HASH is the globally-unique part, so this is the reliable key — WITHOUT it, outbound
        (and @lid-vs-@c.us inbound) read as unknown and the reconcile re-imports duplicates.
        (Mirrors the fallback in `_waha_find_message`.)"""
        if not uid:
            return False
        uids = [uid]
        if '_' in uid:
            uids.append(uid.rsplit('_', 1)[-1])
        return bool(self.env['whatsapp.message'].sudo().search_count([('msg_uid', 'in', uids)]))

    def _waha_message_known(self, waha_message):
        return self._waha_uid_exists(self._waha_extract_id(waha_message.get('id')))

    def _waha_process_incoming(self, payload):
        self.ensure_one()
        if payload.get('fromMe'):
            # Echoes of our own sent messages / phone-sent outbound are handled via history import.
            return
        msg_uid = self._waha_extract_id(payload.get('id'))
        if not msg_uid:
            return
        from_contact = payload.get('from') or ''
        group_chat_id = self._waha_group_chat_id(payload)
        if from_contact in ('status@broadcast',) or (from_contact and from_contact.endswith('@newsletter')):
            return
        # Serialize concurrent deliveries of the same message across workers.
        self.env.cr.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (msg_uid,))
        if self._waha_uid_exists(msg_uid):
            return
        if group_chat_id:
            group = self.env['whatsapp.waha.group'].sudo().search([
                ('account_id', '=', self.id), ('chat_id', '=', group_chat_id),
                ('enabled', '=', True), ('available', '=', True)], limit=1)
            if not group:
                return
            channel = self.env['discuss.channel']._waha_get_group_channel(self, group, create_if_not_found=True)
            was_empty = not channel.message_ids
            content = self._waha_build_content(payload)
            if not content.get('body') and not content.get('attachments'):
                if payload.get('hasMedia'):
                    content['body'] = plaintext2html('[%s]' % _('media message could not be downloaded'))
                else:
                    return
            sender_name = payload.get('notifyName') or (payload.get('_data') or {}).get('notifyName')
            participant = self._waha_participant_jid(payload)
            partner = self._waha_resolve_participant(participant, payload, sender_name)
            if not partner:
                content = self._waha_group_sender_prefix(content, sender_name, participant)
                partner = self.env.ref('base.partner_root')
            try:
                with self.env.cr.savepoint():
                    channel.message_post(
                        whatsapp_inbound_msg_uid=msg_uid,
                        waha_live_inbound=True,
                        message_type='whatsapp_message', author_id=partner.id,
                        subtype_xmlid='mail.mt_comment', **content)
            except Exception:
                _logger.exception('WAHA: failed posting inbound group message %s', msg_uid)
                return
            if was_empty and self.waha_auto_import_history:
                self._waha_sync_channel_history(channel, deep=True, limit=self.waha_history_limit)
            return
        if not from_contact:
            return
        number = self._waha_resolve_number(from_contact, payload)
        if not number:
            # Avoid logging the full sender JID (PII) at INFO; keep only a masked tail.
            _logger.debug("WAHA: could not resolve a phone number from %s", from_contact)
            return
        formatted = self._format_incoming_from_number(number)
        sender_name = payload.get('notifyName') or (payload.get('_data') or {}).get('notifyName')
        channel = self._find_active_channel(formatted, sender_name=sender_name, create_if_not_found=True)
        if not channel:
            return
        self._waha_grant_members(channel)
        was_empty = not channel.message_ids
        content = self._waha_build_content(payload)
        if not content.get('body') and not content.get('attachments'):
            if payload.get('hasMedia'):
                # Real media we could not fetch — keep it visible rather than losing it.
                content['body'] = plaintext2html('[%s]' % _("media message could not be downloaded"))
            else:
                # Nothing renderable: a WhatsApp protocol/system notification (E2E session
                # setup, a revoked-message stub, an unsupported type) — not a real message.
                # Posting it would create an EMPTY message, which Discuss renders as
                # "This message has been removed", cluttering the chat with phantom entries.
                _logger.debug("WAHA: skipping content-less message %s (type=%s)",
                              msg_uid, payload.get('type'))
                return
        try:
            with self.env.cr.savepoint():
                channel.message_post(
                    whatsapp_inbound_msg_uid=msg_uid,
                    waha_live_inbound=True,
                    message_type='whatsapp_message',
                    author_id=channel.whatsapp_partner_id.id,
                    subtype_xmlid='mail.mt_comment',
                    **content,
                )
        except Exception:
            _logger.exception("WAHA: failed posting inbound message %s", msg_uid)
            return
        if was_empty and self.waha_auto_import_history:
            try:
                self._waha_sync_channel_history(channel, deep=True, limit=self.waha_history_limit)
            except Exception:
                _logger.exception("WAHA: auto history import failed for channel %s", channel.id)

    def _waha_find_message(self, raw_id):
        """Find a whatsapp.message for a WAHA event id.

        Inbound msg_uids are stored as WAHA's full serialized id
        (``fromMe_remoteJid_HASH``); outbound msg_uids come from the send response
        as the bare HASH. Acks/reactions always reference messages by the full
        serialized id, so fall back to the trailing hash segment to match our own
        outbound messages (otherwise ticks never advance past 'sent' and reactions
        on our messages are dropped).
        """
        uid = self._waha_extract_id(raw_id)
        Msg = self.env['whatsapp.message'].sudo()
        if not uid:
            return Msg
        message = Msg.search([('msg_uid', '=', uid)], limit=1)
        if not message and '_' in uid:
            message = Msg.search([('msg_uid', '=', uid.rsplit('_', 1)[-1])], limit=1)
        return message

    def _waha_process_ack(self, payload):
        self.ensure_one()
        state = {1: 'sent', 2: 'delivered', 3: 'read', 4: 'read', -1: 'error'}.get(payload.get('ack'))
        if not state:
            return
        message = self._waha_find_message(payload.get('id'))
        if not message:
            return
        rank = {'outgoing': 0, 'sent': 1, 'delivered': 2, 'read': 3}
        changed = False
        if state == 'error' and message.state != 'error':
            message.state = 'error'
            changed = True
        elif rank.get(state, 0) > rank.get(message.state, -1):
            message.state = state
            changed = True
        if not changed:
            return
        message._update_message_fetched_seen()
        # Push the new tick to open clients live: whatsappStatus (the ✓/✓✓/read tick) is
        # otherwise only refreshed on reload, so a webhook ack wouldn't update the bubble.
        mail_msg = message.mail_message_id
        if mail_msg:
            Store(bus_channel=mail_msg._bus_channel()).add(
                mail_msg, {'whatsappStatus': message.state}).bus_send()

    def _waha_process_reaction(self, payload):
        """Reflect an inbound WhatsApp reaction (customer added/removed an emoji) onto
        the corresponding Discuss message, using the standard whatsapp helper."""
        self.ensure_one()
        if payload.get('fromMe'):
            # Our own reaction echoed back; the Odoo-side reaction already exists.
            return
        reaction = payload.get('reaction') or {}
        wa_message = self._waha_find_message(reaction.get('messageId') or reaction.get('id'))
        mail_message = wa_message.mail_message_id
        if not mail_message or mail_message.model != 'discuss.channel':
            return
        channel = self.env['discuss.channel'].sudo().browse(mail_message.res_id)
        participant = self._waha_participant_jid(payload) if channel.is_waha_group_channel else ''
        partner = self._waha_resolve_participant(
            participant, payload, payload.get('notifyName') or (payload.get('_data') or {}).get('notifyName')) \
            if participant else channel.whatsapp_partner_id
        if not partner:
            return
        # reaction.text carries the emoji; empty string means the reaction was removed.
        mail_message._post_whatsapp_reaction(
            reaction_content=reaction.get('text') or '', partner_id=partner)

    def _waha_process_session_status(self, payload):
        self.ensure_one()
        self._waha_apply_status(payload.get('status'), payload.get('me') or {})

    # ------------------------------------------------------------------
    # History import (backfill)
    # ------------------------------------------------------------------
    def _waha_fetch_messages(self, chat_id, limit=50, offset=0):
        self.ensure_one()
        data = self._waha_request(
            f'{self.waha_session}/chats/{chat_id}/messages', 'GET',
            params={'limit': limit, 'offset': offset, 'downloadMedia': 'true'})
        return data if isinstance(data, list) else []

    def _waha_sync_channel_history(self, channel, deep=False, limit=None):
        """Smart backfill: if the last 2 WAHA messages are already known, do nothing;
        otherwise import missing messages oldest-first (deduped, idempotent)."""
        self.ensure_one()
        if not channel.whatsapp_number and not channel.waha_chat_id:
            return 0
        chat_id = channel.waha_chat_id or self._waha_chat_id(channel.whatsapp_number)
        if not chat_id:
            return 0
        if not deep:
            recent = self._waha_fetch_messages(chat_id, limit=2, offset=0)
            if recent and all(self._waha_message_known(m) for m in recent):
                return 0  # in sync
        max_import = limit or self.waha_history_limit or 200
        offset, page, imported = 0, 100, 0
        while offset < 5000:
            batch = self._waha_fetch_messages(chat_id, limit=page, offset=offset)
            if not batch:
                break
            fresh = [m for m in batch if not self._waha_message_known(m)]
            if not fresh:
                break  # reached the already-synced region
            # No "imported earlier conversation" separator: with WAHA channels now ordered
            # by message date (thread_patch.js), imported messages slot into their correct
            # chronological position, so a separator note would be misplaced and confusing.
            imported += self._waha_import_messages(channel, list(reversed(fresh)))
            if imported >= max_import:
                break
            offset += page
        return imported

    def _waha_import_messages(self, channel, waha_messages):
        """Import a list of WAHA messages (already oldest-first) into a Discuss channel
        without re-sending them. Returns the number actually imported."""
        self.ensure_one()
        # Messages sent from the WhatsApp number itself (from the phone / before the
        # integration) carry NO sender identity — WhatsApp never tells us which employee
        # sent them. Crediting an arbitrary user (this used to be the first Default User)
        # falsely attributes everyone's history to one person, so attribute imported
        # outbound messages to OdooBot, making clear they came from the WAHA number.
        operator = self.env.ref('base.partner_root')
        count = 0
        for waha_message in waha_messages:
            uid = self._waha_extract_id(waha_message.get('id'))
            if not uid or self._waha_uid_exists(uid):
                continue
            content = dict(self._waha_build_content(waha_message))
            if not content.get('body') and not content.get('attachments'):
                if waha_message.get('hasMedia'):
                    content['body'] = plaintext2html('[%s]' % _("media message could not be downloaded"))
                else:
                    # Same rule as the live path: never import a content-less protocol/system
                    # entry, which would show up as "This message has been removed".
                    continue
            ts = waha_message.get('timestamp')
            if ts:
                try:
                    content['date'] = fields.Datetime.to_string(
                        datetime.fromtimestamp(int(ts), tz=timezone.utc).replace(tzinfo=None))
                except (ValueError, TypeError, OSError):
                    pass
            try:
                with self.env.cr.savepoint():
                    if waha_message.get('fromMe'):
                        channel.message_post(
                            whatsapp_outbound_msg_uid=uid,
                            message_type='whatsapp_message',
                            author_id=operator.id,
                            subtype_xmlid='mail.mt_note',
                            **content,
                        )
                    else:
                        partner = channel.whatsapp_partner_id
                        if channel.is_waha_group_channel:
                            participant = self._waha_participant_jid(waha_message)
                            sender_name = waha_message.get('notifyName') or (waha_message.get('_data') or {}).get('notifyName')
                            partner = self._waha_resolve_participant(participant, waha_message, sender_name)
                            if not partner:
                                content = self._waha_group_sender_prefix(content, sender_name, participant)
                                partner = operator
                        channel.message_post(
                            whatsapp_inbound_msg_uid=uid,
                            message_type='whatsapp_message',
                            author_id=partner.id,
                            subtype_xmlid='mail.mt_note',
                            **content,
                        )
                count += 1
            except Exception:
                _logger.exception("WAHA: failed importing message %s", uid)
        return count

    def _waha_fetch_overview(self, limit=WAHA_RECONCILE_MAX_CHATS):
        """WAHA chat overview: the most-recently-active chats, each with its last message.
        Used to discover conversations to catch up on — including numbers that first
        messaged during an outage (which have no Discuss channel yet)."""
        self.ensure_one()
        data = self._waha_request(
            f'{self.waha_session}/chats/overview', 'GET', params={'limit': limit})
        return data if isinstance(data, list) else []

    def _waha_reconcile_overview(self):
        """Catch-up sync driven by WAHA's chat overview. For every 1:1 chat with recent
        activity that has any un-imported message in its recent tail, ensure a channel
        exists and backfill the gap. This is the reliable recovery for messages missed
        while the session was disconnected: the live `message` webhook can drop events
        during an outage and never fires at all for a number that first messaged during
        it, so scanning the overview (not just existing channels) closes both gaps. Checks
        the recent TAIL (not only the last message) so a gap sitting below a later reply is
        still caught. Idempotent."""
        self.ensure_one()
        try:
            overview = self._waha_fetch_overview()
        except Exception:
            _logger.exception("WAHA: chat overview fetch failed for %s", self.waha_session)
            return 0
        cutoff = int((fields.Datetime.now() - timedelta(hours=WAHA_RECONCILE_WINDOW_HOURS)).timestamp())
        imported = 0
        for chat in overview:
            chat_id_raw = (chat or {}).get('id') or ''
            if chat_id_raw.endswith('@g.us'):
                group = self.env['whatsapp.waha.group'].sudo().search([
                    ('account_id', '=', self.id), ('chat_id', '=', chat_id_raw),
                    ('enabled', '=', True), ('available', '=', True)], limit=1)
                if not group:
                    continue
                channel = self.env['discuss.channel']._waha_get_group_channel(self, group, create_if_not_found=True)
                try:
                    imported += self._waha_sync_channel_history(channel, deep=True)
                except Exception:
                    _logger.exception('WAHA: reconcile-overview failed for group %s', chat_id_raw)
                continue
            if not chat_id_raw.endswith('@c.us'):
                continue
            last = chat.get('lastMessage') or {}
            ts = last.get('timestamp')
            if ts and int(ts) < cutoff:
                continue  # no recent activity — not an outage gap
            number = re.sub(r'\D', '', chat_id_raw.split('@')[0])
            if not number:
                continue
            chat_id = self._waha_chat_id(number)
            # Inspect the recent TAIL, not just the last message: a message missed during an
            # outage often sits BELOW a later (known) reply, so a last-message-only check
            # would skip the whole chat and leave the gap unrecovered.
            try:
                tail = self._waha_fetch_messages(chat_id, limit=WAHA_RECONCILE_TAIL, offset=0)
            except Exception:
                _logger.exception("WAHA: reconcile tail fetch failed for %s", chat_id)
                continue
            if not tail or all(self._waha_message_known(m) for m in tail):
                continue  # fully in sync
            try:
                formatted = self._format_incoming_from_number(number)
                channel = self._find_active_channel(
                    formatted, sender_name=chat.get('name'), create_if_not_found=True)
                if not channel:
                    continue
                self._waha_grant_members(channel)
                imported += self._waha_sync_channel_history(channel, deep=True)
            except Exception:
                _logger.exception("WAHA: reconcile-overview failed for chat %s", chat_id_raw)
        if imported:
            _logger.info("WAHA: reconcile imported %s missed message(s) for %s", imported, self.waha_session)
        return imported

    def _waha_reconcile_channels(self):
        for account in self.filtered(lambda a: a.provider == 'waha' and a.waha_status == 'working'):
            try:
                account._waha_reconcile_overview()
            except Exception:
                _logger.exception("WAHA: reconcile failed for account %s", account.id)

    # ------------------------------------------------------------------
    # Account protection (anti-ban send guards)
    # ------------------------------------------------------------------
    def _waha_day_start_utc(self, user):
        """Start of 'today' in the user's timezone, returned as a naive UTC datetime."""
        tz_name = (user.tz or self.env.company.partner_id.tz or 'Asia/Riyadh')
        tz = pytz.timezone(tz_name)
        now_local = pytz.utc.localize(fields.Datetime.now()).astimezone(tz)
        day_start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        return day_start_local.astimezone(pytz.utc).replace(tzinfo=None)

    def _waha_channel_messages(self, channel, message_type):
        return self.env['whatsapp.message'].sudo().search([
            ('mail_message_id.model', '=', 'discuss.channel'),
            ('mail_message_id.res_id', '=', channel.id),
            ('message_type', '=', message_type),
        ])

    def _waha_user_new_number_count_today(self, user):
        """Distinct WAHA numbers this user first-contacted today that have not replied
        (cold outreach) — the counter for the per-user daily new-number cap."""
        self.ensure_one()
        self.env.cr.execute("""
            SELECT count(DISTINCT dc.id)
            FROM discuss_channel dc
            JOIN mail_message mm ON mm.model = 'discuss.channel' AND mm.res_id = dc.id
            JOIN whatsapp_message wm ON wm.mail_message_id = mm.id AND wm.message_type = 'outbound'
            WHERE dc.wa_account_id = %s
              AND mm.author_id = %s
              AND mm.create_date >= %s
              AND NOT EXISTS (
                  SELECT 1 FROM mail_message mi
                  JOIN whatsapp_message wi ON wi.mail_message_id = mi.id AND wi.message_type = 'inbound'
                  WHERE mi.model = 'discuss.channel' AND mi.res_id = dc.id
              )
        """, (self.id, user.partner_id.id, self._waha_day_start_utc(user)))
        return self.env.cr.fetchone()[0]

    def _waha_effective_ratio(self):
        """Send/receive ratio, aged up from the stored base by account age."""
        self.ensure_one()
        ratio = self.waha_balance_ratio or 0.0
        if self.waha_working_since and self.waha_balance_ratio_step:
            weeks = (fields.Datetime.now() - self.waha_working_since).days // 7
            aged = 1.0 + weeks * self.waha_balance_ratio_step
            ratio = max(ratio, min(self.waha_balance_ratio_max or aged, aged))
        return ratio

    def _waha_check_send_allowed(self, number, user, channel=None, check_new=True):
        """Fail-open wrapper: only the intentional protection blocks propagate; any
        internal error in the guard must never block a real send."""
        try:
            self._waha_check_send_allowed_inner(number, user, channel=channel, check_new=check_new)
        except WahaSendLimit as err:
            # Surface as a plain UserError: a custom UserError subclass serializes under
            # its own class name, which the web client doesn't recognize and renders as a
            # raw 'Server Error' traceback instead of a clean warning dialog.
            raise UserError(str(err)) from None
        except Exception:
            _logger.exception("WAHA send-guard error — allowing the send")

    def _waha_check_send_allowed_inner(self, number, user, channel=None, check_new=True):
        """Raise WahaSendLimit if an outbound WAHA message to `number` violates a
        protection rule. Raising rolls back the request, so nothing is left as a
        failed message in the conversation."""
        self.ensure_one()
        if self.provider != 'waha' or not number:
            return
        Channel = self.env['discuss.channel'].sudo()
        formatted = Channel._get_whatsapp_channel_format_number(number)
        if channel is None:
            channel = Channel.search([
                ('channel_type', '=', 'whatsapp'), ('wa_account_id', '=', self.id),
                ('whatsapp_number', '=', formatted)], limit=1)
        has_inbound = bool(channel) and bool(self._waha_channel_messages(channel, 'inbound'))
        is_new = not channel or not channel.message_ids

        # Rule A — a contact that never replied may be messaged only once per window.
        if channel and not has_inbound:
            hours = self.waha_cold_resend_hours or 0
            if hours:
                last_out = self._waha_channel_messages(channel, 'outbound').sorted('id', reverse=True)[:1]
                if last_out and (fields.Datetime.now() - last_out.create_date) < timedelta(hours=hours):
                    raise WahaSendLimit(_(
                        "This contact hasn't replied yet. To keep the WhatsApp number safe, "
                        "you can message it again after %(hours)s hours — or send now via the "
                        "official WhatsApp.", hours=hours))

        # Rule B — per-user daily cap on brand-new numbers.
        if check_new and is_new:
            limit = self.waha_new_number_daily_limit or 0
            if limit and self._waha_user_new_number_count_today(user) >= limit:
                raise WahaNewNumberLimit(_(
                    "You've reached today's limit of %(limit)s new WhatsApp numbers on this "
                    "(unofficial) account. Please contact new numbers via the official WhatsApp.",
                    limit=limit))

        # Rule C — global conversational balance over the window.
        window = self.waha_balance_window_hours or 24
        since = fields.Datetime.now() - timedelta(hours=window)
        Msg = self.env['whatsapp.message'].sudo()
        out_count = Msg.search_count([
            ('wa_account_id', '=', self.id), ('message_type', '=', 'outbound'),
            ('create_date', '>=', since)])
        in_count = Msg.search_count([
            ('wa_account_id', '=', self.id), ('message_type', '=', 'inbound'),
            ('create_date', '>=', since)])
        allowed = (self.waha_balance_base or 0) + self._waha_effective_ratio() * in_count
        if out_count >= allowed:
            raise WahaSendLimit(_(
                "This WhatsApp number has reached its safe sending balance for now (protecting it "
                "from being blocked). It allows more as customers reply — meanwhile, send via the "
                "official WhatsApp."))

    # ------------------------------------------------------------------
    # Account health monitoring
    # ------------------------------------------------------------------
    def _waha_health_data(self):
        """Proxy account-health KPIs + score (0-100) over a 7-day window, since WAHA
        exposes no official quality rating. Signals: delivery/read conversion,
        outbound error rate, session status, and status flapping."""
        self.ensure_one()
        since = fields.Datetime.now() - timedelta(days=7)
        Msg = self.env['whatsapp.message'].sudo()

        # Window on the message's real WhatsApp date, NOT create_date: back-filled
        # history is inserted now but carries an OLD date, so it falls outside the
        # window and never pollutes the live-send delivery/error KPIs.
        def cnt(extra):
            return Msg.search_count(
                [('wa_account_id', '=', self.id), ('mail_message_id.date', '>=', since)] + extra)

        outbound = cnt([('message_type', '=', 'outbound')])
        inbound = cnt([('message_type', '=', 'inbound')])
        sent_plus = cnt([('message_type', '=', 'outbound'), ('state', 'in', ('sent', 'delivered', 'read'))])
        delivered = cnt([('message_type', '=', 'outbound'), ('state', 'in', ('delivered', 'read'))])
        read = cnt([('message_type', '=', 'outbound'), ('state', '=', 'read')])
        error = cnt([('message_type', '=', 'outbound'), ('state', 'in', ('error', 'bounced'))])
        # Delivered % and Read % are one funnel over the SAME base (messages WhatsApp
        # accepted = sent_plus). Since read ⊆ delivered ⊆ sent_plus, this guarantees
        # read_rate <= delivered_rate <= 1 — i.e. Read can never look higher than
        # Delivered. (Read used to divide by `delivered`, a different denominator, which
        # made a conditional 'of-delivered' rate that could exceed Delivered %.)
        delivered_rate = (delivered / sent_plus) if sent_plus else (0.0 if outbound else 1.0)
        read_rate = (read / sent_plus) if sent_plus else 0.0
        error_rate = (error / outbound) if outbound else 0.0

        score = 100
        reasons = []  # (points, human-readable cause)
        if self.waha_status != 'working':
            score -= 60
            reasons.append((-60, _("Session is not connected (status: %s).", self.waha_status or 'unknown')))
        err_pen = min(40, round(error_rate * 100))
        if err_pen:
            score -= err_pen
            reasons.append((-err_pen, _("%(pct).0f%% of the last 7 days' outbound messages failed (%(err)s of %(out)s).",
                                        pct=error_rate * 100, err=error, out=outbound)))
        if outbound >= 5:
            # WAHA (unofficial) delivery receipts (ack 2) are frequently delayed or never
            # arrive, so many genuinely-delivered messages stay at 'sent'. A moderate
            # confirmed-delivery rate is therefore NORMAL. The two tiers below (thresholds
            # and penalties) are CONFIGURABLE per account on the Health page; each tier is
            # disabled by setting its threshold or its penalty to 0. Defaults are lenient.
            # (The KPI itself stays honest: it shows confirmed deliveries.)
            dr_pct = delivered_rate * 100
            crit_pct = self.waha_health_deliv_crit_pct or 0
            crit_pen = self.waha_health_deliv_crit_penalty or 0
            warn_pct = self.waha_health_deliv_warn_pct or 0
            warn_pen = self.waha_health_deliv_warn_penalty or 0
            if crit_pct and crit_pen and dr_pct < crit_pct:
                score -= crit_pen
                reasons.append((-crit_pen, _("Very low delivery: only %.0f%% of sent messages were confirmed delivered.", dr_pct)))
            elif warn_pct and warn_pen and dr_pct < warn_pct:
                score -= warn_pen
                reasons.append((-warn_pen, _("Few delivery confirmations: %(pct).0f%% (note: WAHA delivery receipts are often delayed, so actual delivery is usually higher).", pct=dr_pct)))
        flap_pen = min(20, (self.waha_flap_count or 0) * 5)
        if flap_pen:
            score -= flap_pen
            reasons.append((-flap_pen, _("Session flapped %s time(s) today (connection unstable).", self.waha_flap_count)))
        score = max(0, min(100, score))
        label = 'good' if score >= 80 else ('warning' if score >= 50 else 'critical')
        if not reasons:
            reasons.append((0, _("All good — session connected, deliveries healthy, no errors.")))
        return {
            'outbound': outbound, 'inbound': inbound, 'delivered_rate': delivered_rate,
            'read_rate': read_rate, 'error_rate': error_rate, 'score': score, 'label': label,
            'reasons': reasons,
        }

    @api.depends('waha_status', 'waha_flap_count', 'waha_health_deliv_warn_pct',
                 'waha_health_deliv_warn_penalty', 'waha_health_deliv_crit_pct',
                 'waha_health_deliv_crit_penalty')
    def _compute_waha_health(self):
        for account in self:
            if account.provider != 'waha':
                account.waha_health_score = 0
                account.waha_health_label = False
                account.waha_kpi_outbound = account.waha_kpi_inbound = 0
                account.waha_kpi_delivered_rate = account.waha_kpi_read_rate = 0.0
                account.waha_kpi_error_rate = 0.0
                account.waha_health_reason = False
                continue
            data = account._waha_health_data()
            account.waha_health_score = data['score']
            account.waha_health_label = data['label']
            account.waha_kpi_outbound = data['outbound']
            account.waha_kpi_inbound = data['inbound']
            account.waha_kpi_delivered_rate = data['delivered_rate'] * 100
            account.waha_kpi_read_rate = data['read_rate'] * 100
            account.waha_kpi_error_rate = data['error_rate'] * 100
            account.waha_health_reason = account._waha_health_reason_html(data)

    def _waha_health_reason_html(self, data):
        """Human-readable breakdown of how the score was reached (the 'why')."""
        rows = []
        for points, text in data.get('reasons', []):
            if points < 0:
                badge = '<span style="color:#b02a37;font-weight:600;">%+d</span>' % points
            elif points > 0:
                badge = '<span style="color:#198754;font-weight:600;">%+d</span>' % points
            else:
                badge = '<span style="color:#198754;font-weight:600;">✓</span>'
            rows.append('<li>%s &nbsp; %s</li>' % (badge, escape(text)))
        head = _("Score %(score)s/100 — starts at 100, then:", score=data['score'])
        tip = _("Tip: it improves as recent errors age out of the 7-day window and as new "
                "messages deliver cleanly.")
        return Markup(
            '<p class="mb-1">%s</p><ul class="mb-1">%s</ul><p class="text-muted small mb-0">%s</p>'
            % (escape(head), ''.join(rows), escape(tip)))

    def _waha_health_alert(self, data):
        """Notify the responsible user(s) that this account's health degraded, as a
        DIRECT MESSAGE from OdooBot — not a chatter post on the account."""
        self.ensure_one()
        # Account-health degradation is an operational/admin concern, not an
        # inbox assignment. It must never interrupt the WAHA reception team.
        admin = self.env.ref('base.user_admin').sudo()
        partners = admin.partner_id if admin.active and not admin.share else self.env['res.partner']
        if not partners:
            return
        body = Markup(
            "<p>⚠️ <b>WhatsApp (WAHA) account “%(name)s” health: %(label)s</b> — score %(score)s/100.</p>"
            "<ul><li>Session status: %(status)s</li>"
            "<li>Delivered: %(dlv).0f%% · Read: %(rd).0f%% · Errors: %(err).0f%%</li>"
            "<li>Last 7 days: %(out)s sent / %(inb)s received · status flaps today: %(flap)s</li></ul>"
            "<p>Consider pausing outbound and routing new outreach to the official WhatsApp.</p>"
        ) % {
            'name': self.name or self.waha_session or 'WAHA',
            'label': (data['label'] or '').upper(), 'score': data['score'],
            'status': self.waha_status or 'unknown',
            'dlv': data['delivered_rate'] * 100, 'rd': data['read_rate'] * 100,
            'err': data['error_rate'] * 100, 'out': data['outbound'], 'inb': data['inbound'],
            'flap': self.waha_flap_count or 0,
        }
        odoobot_id = self.env['ir.model.data']._xmlid_to_res_id('base.partner_root')
        Channel = self.env['discuss.channel'].sudo()
        for partner in partners:
            channel = Channel._get_or_create_chat([odoobot_id, partner.id])
            channel.message_post(
                author_id=odoobot_id,
                body=body,
                message_type='comment',
                subtype_xmlid='mail.mt_comment',
            )

    # ------------------------------------------------------------------
    # Cron entrypoints
    # ------------------------------------------------------------------
    @api.model
    def _cron_waha_refresh_status(self):
        self.search([('provider', '=', 'waha')]).action_waha_refresh_status()

    @api.model
    def _cron_waha_health_check(self):
        today = fields.Date.context_today(self)
        for account in self.search([('provider', '=', 'waha')]):
            data = account._waha_health_data()
            # Warning is informational in the account form. Only a critical score
            # is operationally dangerous enough to interrupt the administrator.
            in_danger = data['label'] == 'critical'
            if in_danger and account.waha_health_alert_date != today:
                try:
                    account._waha_health_alert(data)
                except Exception:
                    _logger.exception("WAHA health alert failed for %s", account.id)
                else:
                    account.write({
                        'waha_health_alerted': True,
                        'waha_health_alert_date': today,
                    })
            elif not in_danger and account.waha_health_alerted:
                account.waha_health_alerted = False

    @api.model
    def _cron_waha_reconcile(self):
        self.search([('provider', '=', 'waha'), ('waha_status', '=', 'working')])._waha_reconcile_channels()

    @api.model
    def _cron_waha_age_adjust(self):
        """Daily: reset the status-flap window and gradually raise each account's
        send/receive ratio as it ages."""
        for account in self.search([('provider', '=', 'waha')]):
            if account.waha_flap_count:
                account.waha_flap_count = 0
            if account.waha_working_since:
                aged = account._waha_effective_ratio()
                if aged > (account.waha_balance_ratio or 0.0):
                    account.waha_balance_ratio = aged

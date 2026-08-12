# -*- coding: utf-8 -*-
"""``sembly.meeting`` — one Odoo record per Sembly meeting.

Two channels write to this model and they converge on ONE record, keyed by the
Sembly meeting id, whichever arrives first:

* **MCP** (``_upsert_from_mcp``) brings metadata, minutes (summary) and the
  structured items. It is the only channel that can backfill history or
  re-fetch on demand.
* **Webhook** (``_upsert_from_webhook``) brings the two things MCP's output
  schema does not carry at all — the transcript and the recording link — plus
  participant emails.

Both upserts are strictly NON-DESTRUCTIVE: they only write a field when the
payload actually carries a value for it. That is what makes the arrival order
irrelevant — a later MCP re-sync can never blank a transcript the webhook
delivered, and a Notes webhook can never blank an MCP-supplied summary.

**This module links a meeting to nothing by itself.** It owns the meeting, the
two channels, the AI plumbing and the chatter mechanics; every actual link
target arrives through a satellite module that fills in the seams below:

===========================  =================================================
``_sembly_link_fields``      the fields whose hand edit claims the record
``_ai_candidate_pools``      the candidate blocks put in front of the model
``_ai_postprocess_links``    derived links (a task implies its project)
``_ai_after_link``           side effects of a successful automatic link
``_has_external_link``       "something else already claimed this meeting"
``_summary_targets``         which records receive the chatter note
===========================  =================================================

``era_sembly_meetings_crm`` (opportunities), ``era_sembly_meetings_tasks``
(projects and tasks) and ``era_sembly_meetings_tickets`` (helpdesk tickets)
each implement them for one model. Nothing here may import or reference
``crm``, ``project`` or ``helpdesk``.
"""
import base64
import json
import logging
import re
import secrets
import time
from datetime import date, datetime, timedelta

import pytz
from markupsafe import escape

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools import config, html_sanitize, plaintext2html

from ..services.sembly_mcp_client import SemblyMcpClient, SemblyMcpError
from .sembly_meeting_item import MCP_ITEM_KEYS

_logger = logging.getLogger(__name__)

DEFAULT_MEETING_URL_TEMPLATE = 'https://webapp.sembly.ai/meeting/{id}'
DEFAULT_EU_MEETING_URL_TEMPLATE = 'https://eu.webapp.sembly.ai/meeting/{id}'
# Sembly's guest-access link, confirmed against a real one: the path segment of
#   .../guest-access/meeting/YTI1ZDE0OWYtZmRmOC00MGI3LTkwZTItN2EyMjgwODU3OGZl
# is base64 of a25d149f-fdf8-40b7-90e2-7a22808578fe — a per-meeting UUID that
# bears NO relation to the numeric id, and that the MCP tools never expose. So
# this template can only be used when something hands us that UUID; it can
# never be derived from a meeting we imported over MCP.
DEFAULT_SHARE_URL_TEMPLATE = 'https://webapp.sembly.ai/guest-access/meeting/{token}'
DEFAULT_AGENT_ID = 1
DEFAULT_TIMEZONE = 'Asia/Riyadh'

# How much of the summary the matcher is allowed to send to the LLM.
SUMMARY_PROMPT_CHARS = 2000
# How much of it the brief condenser may read — more, because its whole job is
# to compress it, and it runs once per meeting rather than once per match.
BRIEF_PROMPT_CHARS = 6000

# list_meetings is capped server-side; a window returning exactly this many is
# indistinguishable from a truncated one. See _backfill_list_window.
LIST_MEETINGS_CAP = 200

# "Does this Sembly text already contain markup?" — see _coerce_html. Matching a
# KNOWN tag rather than a bare '<' keeps a plain "revenue < 100k" as plain text.
HTML_TAG_RE = re.compile(
    r'</?(?:p|br|hr|b|i|u|em|strong|h[1-6]|ul|ol|li|div|span|a|img|table|'
    r'thead|tbody|tr|td|th|blockquote|pre|code)\b[^>]*>', re.IGNORECASE)

# Sembly does not publish its Custom Automation schema, and its helpdesk is not
# publicly fetchable, so these are candidate spellings rather than a contract.
# A payload that matches none of them is logged WITH ITS KEYS (never its
# values) by the controller, which is what turns the next real webhook into the
# answer instead of another silent rejection.
WEBHOOK_ID_KEYS = ('meeting_id', 'id', 'meetingId', 'meeting_uuid', 'uuid')
WEBHOOK_LINK_KEYS = (
    'meeting_link', 'share_link', 'meeting_share_link', 'shared_link',
    'share_url', 'meeting_url', 'link', 'url',
)
# Public mailbox providers. An address here says NOTHING either way about a
# meeting being internal: our own staff use them, and so do external contacts.
# They are excluded from the domain test entirely rather than counted as
# external, which would veto internal meetings for the wrong reason.
FREE_EMAIL_DOMAINS = {
    'gmail.com', 'googlemail.com', 'hotmail.com', 'hotmail.co.uk', 'outlook.com',
    'outlook.sa', 'live.com', 'msn.com', 'yahoo.com', 'yahoo.co.uk', 'ymail.com',
    'icloud.com', 'me.com', 'mac.com', 'aol.com', 'gmx.com', 'gmx.de',
    'proton.me', 'protonmail.com', 'pm.me', 'tutanota.com', 'zoho.com',
    'yandex.com', 'yandex.ru', 'mail.ru', 'qq.com', '163.com', '126.com',
}

EMAIL_DOMAIN_RE = re.compile(r'[^@\s<>,;]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})')

# A UUID here would let us BUILD the guest link, so it is worth looking for.
WEBHOOK_UUID_KEYS = ('meeting_uuid', 'uuid', 'guid', 'share_token', 'public_id')
UUID_RE = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.IGNORECASE)

# What an employee may READ but never CHANGE.
#
# Access control here is deliberately split in two, because the two groups
# differ along two different axes and no single mechanism expresses both:
#
# * WHICH RECORDS   — an ACL + record rule question. Both groups see every
#                     meeting; only a manager may create or delete one.
# * WHICH FIELDS    — not expressible as an ACL, which is per-model. An
#                     employee must be able to file a meeting against an
#                     opportunity / task / ticket, and must not be able to
#                     rewrite what the meeting says. So the write is checked
#                     field by field, in ``_check_content_access``.
#
# This is a DENY list on purpose: a link field contributed by a satellite
# module is writable by an employee without that module having to register
# anything, while everything the meeting factually IS stays manager-only.
CONTENT_FIELDS = {
    'sembly_meeting_id', 'name', 'active', 'company_id',
    'started_at', 'finished_at', 'duration_seconds',
    'platform', 'owner_name', 'owner_email', 'workspace_id', 'team_name',
    'meeting_type', 'participant_names', 'participant_emails', 'partner_ids',
    'summary', 'transcript', 'ai_brief', 'meeting_url', 'share_url', 'item_ids',
    'source', 'raw_payload',
}

LINK_STATES = [
    ('unlinked', "غير مرتبط"),
    ('suggested', "مقترح"),
    ('auto', "مرتبط آلياً"),
    ('manual', "مرتبط يدوياً"),
]

# Tokens too generic to identify anything. Arabic + Latin.
STOP_WORDS = {
    'the', 'and', 'for', 'with', 'call', 'meet', 'meeting', 'sync', 'weekly',
    'daily', 'review', 'discussion', 'catch', 'standup', 'demo', 'intro',
    'follow', 'followup', 'update', 'session', 'zoom', 'teams', 'google',
    'اجتماع', 'الاجتماع', 'اجتماعات', 'مع', 'من', 'الى', 'إلى', 'عن', 'في',
    'على', 'متابعة', 'مناقشة', 'مكالمة', 'يومي', 'اسبوعي', 'أسبوعي', 'عرض',
}


def _sembly_post_init(env):
    """Generate the webhook path secret once, at install time.

    It is the webhook's only authentication mechanism (Sembly's automations
    send no signature header), so it must be unguessable and must never be
    shipped as a static default in an XML data file.
    """
    icp = env['ir.config_parameter'].sudo()
    if not icp.get_param('sembly.webhook_token'):
        icp.set_param('sembly.webhook_token', secrets.token_urlsafe(32))


class SemblyMeeting(models.Model):
    _name = 'sembly.meeting'
    _description = "Sembly Meeting"
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'started_at desc, id desc'
    _rec_name = 'name'

    # ------------------------------------------------------------------ identity
    sembly_meeting_id = fields.Char(
        string="معرّف Sembly", required=True, index=True, copy=False, tracking=True,
        help="The Sembly meeting id. Both channels supply it; it is what makes "
             "the two sources converge on one record.")
    name = fields.Char(string="عنوان الاجتماع", required=True, tracking=True)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        'res.company', string="الشركة", required=True, index=True,
        default=lambda self: self.env.company)

    # ------------------------------------------------------------------ timing
    started_at = fields.Datetime(string="بدأ في", index=True, tracking=True)
    finished_at = fields.Datetime(string="انتهى في")
    duration_seconds = fields.Integer(string="المدة (ثانية)")
    duration_display = fields.Char(
        string="المدة", compute='_compute_duration_display', store=True)

    # ------------------------------------------------------------------ context
    platform = fields.Char(string="المنصة")
    owner_name = fields.Char(string="منظّم الاجتماع")
    owner_email = fields.Char(string="بريد المنظّم")
    workspace_id = fields.Char(string="مساحة العمل")
    team_name = fields.Char(string="الفريق")
    meeting_type = fields.Char(string="نوع الاجتماع")
    participant_names = fields.Text(string="المشاركون (أسماء)")
    participant_emails = fields.Text(string="المشاركون (بريد)")
    partner_ids = fields.Many2many(
        'res.partner', 'sembly_meeting_partner_rel', 'meeting_id', 'partner_id',
        string="جهات الاتصال",
        help="Resolved from participant emails first, then from names.")

    # ------------------------------------------------------------------ content
    summary = fields.Html(string="الملخص", sanitize=True)
    transcript = fields.Text(
        string="التفريغ النصي",
        help="Delivered by the Sembly Transcription automation (webhook) only — "
             "the MCP output schema carries no transcript.")
    meeting_url = fields.Char(
        string="رابط الاجتماع في Sembly",
        help="The workspace link. It REQUIRES a Sembly account with access to "
             "the meeting, which is why share_url exists.")
    share_url = fields.Char(
        string="رابط المشاركة (ضيف)",
        help="Sembly's guest-access link: anyone holding it can open the "
             "meeting WITHOUT a Sembly account. Sembly keys it by a per-meeting "
             "UUID that the MCP tools do not expose at all, so it cannot be "
             "derived from the meeting id — it arrives with the webhook, or is "
             "pasted here from Sembly's Share dialog.")
    media_url = fields.Char(
        string="رابط التسجيل", compute='_compute_media_url', store=True,
        help="Sembly publishes no direct audio/video file URL; the recording is "
             "played on the meeting page. Kept as its own field so that if "
             "Sembly ever exposes a media URL only this compute changes.")
    item_ids = fields.One2many(
        'sembly.meeting.item', 'meeting_id', string="العناصر")
    item_count = fields.Integer(compute='_compute_item_count')

    # ------------------------------------------------------------------ linking
    # The link TARGETS (opportunity, project, task, ticket) are contributed by
    # the satellite modules; this module owns only the state of the linking.
    link_state = fields.Selection(
        LINK_STATES, string="حالة الربط", default='unlinked',
        required=True, index=True, tracking=True)
    ai_confidence = fields.Float(string="ثقة الذكاء الاصطناعي", digits=(3, 2))
    ai_reasoning = fields.Text(string="تعليل الذكاء الاصطناعي")
    ai_matched_on = fields.Char(
        string="أساس المطابقة",
        help="Which candidate pools were searched, and whether any were "
             "truncated by the caps — so a cut-off candidate set is never invisible.")
    ai_last_attempt = fields.Datetime(
        string="آخر محاولة ربط", copy=False, index=True,
        help="Stamped on every match attempt, successful or not, so the batch "
             "cron can order by it: never-tried meetings first, repeat "
             "failures last. Without it a meeting the model can never link "
             "sits at the head of the queue forever and starves the rest.")
    ai_match_queued = fields.Boolean(
        string="في طابور الربط", default=False, copy=False, index=True,
        help="Set by the list's bulk action. The queue cron picks these up "
             "within seconds and clears the flag as it goes. A queue, not an "
             "inline loop: every match is one LLM round trip, so a large "
             "selection would blow the HTTP worker's 240s limit exactly like "
             "the historical import used to.")

    # ------------------------------------------------------------------ chatter
    ai_brief = fields.Html(
        string="الملخص التنفيذي", sanitize=True, copy=False,
        help="What actually lands in the chatter of the linked record: the "
             "meeting summary condensed by the AI agent into the client's "
             "situation, positives, negatives/risks, scope of work and the "
             "next step. Generated once, on first posting, and reused. If "
             "generation fails the raw summary is posted instead — a brief "
             "must never block the note.")
    summary_posted = fields.Boolean(
        string="نُشر الملخص", default=False, copy=False,
        help="Idempotency guard for requirement 6 — a re-sync never double-posts.")
    summary_posted_on = fields.Datetime(string="تاريخ نشر الملخص", copy=False)

    # ------------------------------------------------------------------ technical
    source = fields.Selection(
        [('mcp', "MCP"), ('webhook', "Webhook"), ('manual', "يدوي")],
        string="المصدر", default='manual', required=True, index=True)
    can_edit_content = fields.Boolean(
        string="يمكن تحرير المحتوى", compute='_compute_can_edit_content',
        help="Drives the readonly state of the content fields in the views. "
             "The views only mirror what _check_content_access enforces on the "
             "server — hiding a field is not access control (rule 19).")
    has_transcript = fields.Boolean(
        string="يحتوي تفريغ", compute='_compute_has_content', store=True)
    has_summary = fields.Boolean(
        string="يحتوي ملخص", compute='_compute_has_content', store=True)
    raw_payload = fields.Text(string="الحمولة الخام (JSON)", copy=False)

    # This is what makes the two channels converge instead of racing: the
    # webhook and the MCP sync both upsert by Sembly id, and a duplicate would
    # split one meeting across two records. Declared as models.Constraint
    # because _sql_constraints is deprecated in Odoo 19 (it still applies the
    # index, but logs "no longer supported" on every registry load).
    _sembly_meeting_id_uniq = models.Constraint(
        'UNIQUE(sembly_meeting_id)',
        "This Sembly meeting is already imported.",
    )

    # ================================================================== computes
    @api.depends('duration_seconds')
    def _compute_duration_display(self):
        for rec in self:
            seconds = rec.duration_seconds or 0
            if not seconds:
                rec.duration_display = ''
                continue
            hours, rem = divmod(int(seconds), 3600)
            minutes = rem // 60
            rec.duration_display = ('%dh %02dm' % (hours, minutes)) if hours else ('%dm' % minutes)

    @api.depends('share_url', 'meeting_url', 'sembly_meeting_id')
    def _compute_media_url(self):
        for rec in self:
            # The guest link first: this one ends up in a chatter note read by
            # people who often have no Sembly account at all, and the workspace
            # link is useless to them. Then the template URL, so a record
            # created by hand still offers something — sembly_meeting_id is
            # required, so it is always buildable.
            rec.media_url = rec.share_url or rec.meeting_url or (
                self._build_meeting_url(rec.sembly_meeting_id)
                if rec.sembly_meeting_id else False)

    @api.depends_context('uid')
    def _compute_can_edit_content(self):
        # depends_context('uid') is required, not decorative: Odoo's field cache
        # is shared across environments within a transaction, so without it the
        # first user to read this field decides the answer for everyone else.
        allowed = self.env.user.has_group('era_sembly_meetings.group_sembly_manager')
        for rec in self:
            rec.can_edit_content = allowed

    @api.depends('transcript', 'summary')
    def _compute_has_content(self):
        for rec in self:
            rec.has_transcript = bool(rec.transcript and rec.transcript.strip())
            # ANY narrative counts, not just Sembly's. A meeting seen only by a
            # second provider has plenty to say, and gating on `summary` alone
            # left those records unable to post to their opportunity at all.
            rec.has_summary = bool(rec._narrative_sources())

    def _compute_item_count(self):
        counts = {}
        if self.ids:
            groups = self.env['sembly.meeting.item']._read_group(
                [('meeting_id', 'in', self.ids)], ['meeting_id'], ['__count'])
            counts = {m.id: c for m, c in groups}
        for rec in self:
            rec.item_count = counts.get(rec.id, 0)

    # ================================================================== helpers
    @api.model
    def _may_commit(self):
        """True outside a test run.

        Committing mid-loop is what lets a cron or an HTTP worker that gets
        killed keep the meetings it already fetched. Inside a test it would
        break the rollback that isolates each case.

        NOT ``registry.in_test_mode()`` — that was removed in Odoo 19 and
        raises AttributeError, which is exactly how it survived unnoticed: the
        only callers were crons, and no test ever ran them.
        """
        return not config['test_enable']

    @api.model
    def _icp(self, key, default=None):
        return self.env['ir.config_parameter'].sudo().get_param(key, default)

    @api.model
    def _icp_int(self, key, default):
        try:
            return int(self._icp(key, default))
        except (TypeError, ValueError):
            return int(default)

    @api.model
    def _icp_float(self, key, default):
        try:
            return float(self._icp(key, default))
        except (TypeError, ValueError):
            return float(default)

    @api.model
    def _tz(self):
        """Timezone for the 'today or yesterday' window of requirement 6.

        res.company has no tz field and this database's company partner has
        none set, so it is an explicit parameter. Era Group is Saudi-based, so
        the default is Asia/Riyadh — the plan's own worked example (21:00
        Riyadh = 18:00 UTC must count as *today*).
        """
        name = self._icp('sembly.timezone') or self.env.user.tz or DEFAULT_TIMEZONE
        try:
            return pytz.timezone(name)
        except pytz.UnknownTimeZoneError:
            return pytz.timezone(DEFAULT_TIMEZONE)

    @api.model
    def _get_client(self):
        """Build an MCP client from env var first, then ir.config_parameter.

        Env var wins so a deployment can keep the token out of the database
        entirely (CLAUDE.md rule 03).
        """
        import os
        token = os.environ.get('SEMBLY_MCP_TOKEN') or self._icp('sembly.mcp_token') or ''
        region = (self._icp('sembly.region') or 'us').lower()
        url = self._icp('sembly.mcp_url') or None
        return SemblyMcpClient(token=token.strip(), region=region, url=url)

    @api.model
    def _parse_dt(self, value):
        """Sembly datetimes -> naive UTC datetime.

        MCP sends ``YYYY-MM-DD HH:MM`` (documented UTC); the webhook sends ISO
        ``YYYY-MM-DDTHH:MM:SS`` possibly with a ``Z`` or an offset.
        """
        if not value:
            return False
        if isinstance(value, datetime):
            return value.replace(tzinfo=None) if value.tzinfo is None else \
                value.astimezone(pytz.UTC).replace(tzinfo=None)
        text = str(value).strip()
        if not text:
            return False
        # The live MCP server appends a timezone LABEL, e.g.
        # "2026-08-10 12:30 (UTC)". Neither fromisoformat nor any strptime
        # format accepts it, so without this the date is silently dropped and
        # the meeting lands with no started_at — which also takes it out of the
        # "today or yesterday" chatter window.
        text = re.sub(r'\s*\([A-Za-z][A-Za-z0-9/_+-]*\)\s*$', '', text)
        text = text.replace('Z', '+00:00')
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%dT%H:%M:%S',
                        '%Y-%m-%dT%H:%M', '%Y-%m-%d'):
                try:
                    parsed = datetime.strptime(text, fmt)
                    break
                except ValueError:
                    continue
            else:
                return False
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(pytz.UTC).replace(tzinfo=None)
        return parsed

    @api.model
    def _build_meeting_url(self, sembly_id):
        template = self._icp('sembly.meeting_url_template')
        if not template:
            region = (self._icp('sembly.region') or 'us').lower()
            template = DEFAULT_EU_MEETING_URL_TEMPLATE if region == 'eu' \
                else DEFAULT_MEETING_URL_TEMPLATE
        try:
            return template.format(id=sembly_id)
        except (KeyError, IndexError):
            return template

    @api.model
    def _build_share_url(self, uuid_value):
        """Build Sembly's guest link from a meeting UUID.

        UNSUPPORTED FORMAT — Sembly support, 2026-08-11: the
        ``guest-access/meeting/<base64 uuid>`` shape "is an internal
        implementation detail rather than a documented, supported contract …
        we would not recommend building against it or depending on it". So
        this is reached ONLY when a payload hands us a real UUID, which nothing
        does today. Do NOT extend it to manufacture guest links out of data we
        already hold: a link that 404s is worse than no link, and it would go
        out in a chatter note.

        The token is base64 of the UUID *string* — a 36-character UUID encodes
        to exactly 48 characters with no padding, which is what a real link
        shows. Returns False for anything that is not a UUID, because guessing
        here would produce a link that 404s for whoever we send it to.
        """
        value = str(uuid_value or '').strip()
        if not UUID_RE.match(value):
            return False
        token = base64.b64encode(value.encode('ascii')).decode('ascii')
        template = self._icp('sembly.share_url_template') or DEFAULT_SHARE_URL_TEMPLATE
        try:
            return template.format(token=token, uuid=value)
        except (KeyError, IndexError):
            return DEFAULT_SHARE_URL_TEMPLATE.format(token=token)

    @api.model
    def _coerce_html(self, text):
        """Sembly text -> sanitized HTML, whichever of the two it already is.

        Sembly's summaries ALREADY contain markup — a real one opens
        ``<p><h2><b>✨ Summary</b></h2></p>``. Running ``plaintext2html`` over
        that escapes every ``<``, so the user reads the tags instead of the
        summary. Running nothing over a genuinely plain note loses its line
        breaks instead. So detect which one it is.

        The test is deliberately conservative — a KNOWN tag, not any ``<`` —
        so a plain sentence like "revenue < 100k" is still treated as text.
        Either way the result is sanitized, because it ends up in a chatter
        note: trusted on the way IN, never on the way out.
        """
        text = (text or '').strip()
        if not text:
            return ''
        body = text if HTML_TAG_RE.search(text) else plaintext2html(text)
        return html_sanitize(body)

    @api.model
    def _render_minutes(self, minutes):
        """``minutes[]`` -> sanitized HTML, one ``<h4>`` section per type."""
        if not minutes:
            return ''
        parts = []
        for entry in minutes:
            if not isinstance(entry, dict):
                continue
            body = self._coerce_html(entry.get('text'))
            if not body:
                continue
            kind = (entry.get('type') or '').strip()
            if kind:
                parts.append('<h4>%s</h4>' % escape(kind))
            parts.append(body)
        return html_sanitize("".join(parts)) if parts else ''

    def _resolve_partners(self):
        """Match participants to res.partner: emails first, then exact names."""
        Partner = self.env['res.partner'].sudo()
        for rec in self:
            partners = Partner.browse()
            emails = [e.strip() for e in re.split(r'[,\n;]', rec.participant_emails or '') if e.strip()]
            if rec.owner_email:
                emails.append(rec.owner_email.strip())
            for email in set(emails):
                if '@' not in email:
                    continue
                partners |= Partner.search([('email', '=ilike', email)], limit=1)
            if not partners:
                names = [n.strip() for n in re.split(r'[,\n;]', rec.participant_names or '') if n.strip()]
                for pname in set(names):
                    if len(pname) < 3:
                        continue
                    partners |= Partner.search([('name', '=ilike', pname)], limit=1)
            if partners:
                rec.partner_ids = [(6, 0, partners.ids)]

    # ================================================================== upserts
    @api.model
    def _find_by_sembly_id(self, sembly_id):
        if not sembly_id:
            return self.browse()
        return self.sudo().with_context(active_test=False).search(
            [('sembly_meeting_id', '=', str(sembly_id))], limit=1)

    @api.model
    def _upsert_from_mcp(self, meta, details=None):
        """Idempotent upsert of one meeting from the MCP channel.

        Only writes fields the payload actually carries, so a re-sync never
        blanks webhook-supplied data.
        """
        if not isinstance(meta, dict):
            return self.browse()
        sembly_id = meta.get('id')
        if sembly_id in (None, ''):
            return self.browse()
        sembly_id = str(sembly_id)

        values = {'source': 'mcp'}
        if meta.get('title'):
            values['name'] = meta['title']
        for key, field_name in (('started_at', 'started_at'), ('finished_at', 'finished_at')):
            parsed = self._parse_dt(meta.get(key))
            if parsed:
                values[field_name] = parsed
        if meta.get('duration_seconds'):
            values['duration_seconds'] = int(meta['duration_seconds'])
        if meta.get('platform'):
            values['platform'] = meta['platform']
        if meta.get('owner_name'):
            values['owner_name'] = meta['owner_name']
        if meta.get('participants'):
            values['participant_names'] = "\n".join(
                str(p) for p in meta['participants'] if p)

        summary_html = ''
        if details:
            summary_html = self._render_minutes(details.get('minutes'))
            if summary_html:
                values['summary'] = summary_html
            values['raw_payload'] = json.dumps(details, ensure_ascii=False)[:200000]

        record = self._find_by_sembly_id(sembly_id)
        if record:
            # Never downgrade a webhook-sourced record's provenance silently:
            # 'webhook' means the transcript channel has been seen for it.
            if record.source == 'webhook':
                values.pop('source', None)
            record.sudo().with_context(sembly_sync=True).write(values)
        else:
            values.update({
                'sembly_meeting_id': sembly_id,
                'name': values.get('name') or _("Sembly meeting %s", sembly_id),
                'meeting_url': self._build_meeting_url(sembly_id),
            })
            record = self.sudo().with_context(sembly_sync=True).create(values)

        if not record.meeting_url:
            record.sudo().write({'meeting_url': self._build_meeting_url(sembly_id)})
        if details:
            record._sync_items_from_mcp(details)
        record._resolve_partners()
        record._queue_delivery_pipeline()
        # Strip sembly_sync before handing the record back: a caller holding it
        # must not silently bypass the manual-override guard in write().
        return record.with_context(sembly_sync=False)

    def _sync_items_from_mcp(self, details):
        """Replace this meeting's MCP-sourced items in one transaction.

        They carry no user state, so delete-and-recreate is both correct and
        the only way a corrected Sembly extraction propagates.
        """
        self.ensure_one()
        Item = self.env['sembly.meeting.item'].sudo()
        rows = []
        for key, item_type in MCP_ITEM_KEYS:
            for entry in details.get(key) or []:
                if not isinstance(entry, dict):
                    continue
                if item_type == 'task':
                    title = (entry.get('title') or '').strip()
                    if not title:
                        continue
                    rows.append({
                        'meeting_id': self.id,
                        'item_type': item_type,
                        'name': title[:500],
                        'description': entry.get('description') or False,
                        'assigned_to': entry.get('assigned_to') or False,
                        'assigned_by': entry.get('assigned_by') or False,
                        'raw_timing': entry.get('timing') or False,
                    })
                else:
                    text = (entry.get('text') or '').strip()
                    if not text:
                        continue
                    rows.append({
                        'meeting_id': self.id,
                        'item_type': item_type,
                        'name': text[:500],
                        'description': entry.get('details') or False,
                    })
        self.item_ids.sudo().unlink()
        if rows:
            Item.create(rows)

    @api.model
    def _is_webhook_test_payload(self, payload):
        """Sembly's "Test" button, told apart from a real delivery.

        Its payload is placeholder data — ``meeting_id: 0``, ``workspace_id:
        0``, ``owner@sembly.ai``, "Meeting Test Title" — and taking it at face
        value creates a junk meeting that then pollutes the list and costs an
        AI match. Every Test click would create another one.

        Deliberately conservative: a zero/absent id AND a sembly.ai address.
        A real meeting always has a positive id, and no customer of ours sends
        one from @sembly.ai — so this cannot swallow real data.
        """
        try:
            meeting_id = int(payload.get('meeting_id') or 0)
        except (TypeError, ValueError):
            meeting_id = 0
        if meeting_id > 0:
            return False
        addresses = "%s %s" % (payload.get('meeting_owner_email') or '',
                               payload.get('automation_owner_email') or '')
        return '@sembly.ai' in addresses.lower()

    @api.model
    def _upsert_from_webhook(self, payload, kind):
        """Upsert from a Sembly Custom Automation POST.

        ``kind`` is one of ``notes`` / ``transcription`` / ``task``. This is the
        only channel that can supply a transcript, a recording link or
        participant emails.
        """
        if not isinstance(payload, dict):
            return self.browse()
        if self._is_webhook_test_payload(payload):
            return self.browse()
        sembly_id = next(
            (payload[key] for key in WEBHOOK_ID_KEYS
             if payload.get(key) not in (None, '')), None)
        if sembly_id in (None, ''):
            return self.browse()
        sembly_id = str(sembly_id)

        values = {}
        title = payload.get('meeting_title') or payload.get('title')
        if title:
            values['name'] = title
        for src, dst in (('meeting_started_at', 'started_at'),
                         ('meeting_finished_at', 'finished_at')):
            parsed = self._parse_dt(payload.get(src))
            if parsed:
                values[dst] = parsed
        if payload.get('meeting_duration'):
            try:
                values['duration_seconds'] = int(payload['meeting_duration'])
            except (TypeError, ValueError):
                pass
        for src, dst in (('meeting_platform', 'platform'),
                         ('meeting_owner', 'owner_name'),
                         ('meeting_owner_email', 'owner_email'),
                         ('workspace_id', 'workspace_id'),
                         ('team_name', 'team_name'),
                         ('meeting_type', 'meeting_type')):
            if payload.get(src):
                values[dst] = payload[src]
        # A guest-access link is the valuable one: it opens without a Sembly
        # account. Take it wherever it appears, and otherwise build it if the
        # payload carries the meeting UUID.
        link = next((payload[key] for key in WEBHOOK_LINK_KEYS
                     if payload.get(key)), None)
        if link and 'guest-access' in str(link):
            values['share_url'] = link
            link = None
        if not values.get('share_url'):
            built = self._build_share_url(next(
                (payload[key] for key in WEBHOOK_UUID_KEYS if payload.get(key)), None))
            if built:
                values['share_url'] = built
        if link:
            # AUTHORITATIVE, and the only real link there is: Sembly's MCP tools
            # return no URL of any kind (verified against the live server —
            # list_meetings and get_meeting expose no link field at all), so a
            # meeting that arrived by MCP alone can only be given the
            # template-built one. Whatever Sembly sends here — a workspace link
            # or a guest/share link — wins over that.
            values['meeting_url'] = link

        participants = payload.get('participants') or payload.get('meeting_participants')
        if participants:
            if isinstance(participants, str):
                joined = participants
            else:
                joined = "\n".join(str(p) for p in participants if p)
            if '@' in joined:
                values['participant_emails'] = joined
            else:
                values['participant_names'] = joined

        transcript = payload.get('meeting_transcription')
        if transcript and str(transcript).strip():
            values['transcript'] = str(transcript)
        notes = payload.get('meeting_notes')
        if notes and str(notes).strip():
            # Sembly's Notes automation sends the same already-HTML body the
            # MCP minutes carry, so it goes through the same coercion.
            values['summary'] = self._coerce_html(str(notes))

        record = self._find_by_sembly_id(sembly_id)
        if record:
            values['source'] = 'webhook'
            record.sudo().with_context(sembly_sync=True).write(values)
        else:
            values.update({
                'sembly_meeting_id': sembly_id,
                'source': 'webhook',
                'name': values.get('name') or _("Sembly meeting %s", sembly_id),
            })
            values.setdefault('meeting_url', self._build_meeting_url(sembly_id))
            record = self.sudo().with_context(sembly_sync=True).create(values)

        record._store_webhook_payload(payload)
        record._queue_delivery_pipeline(require_recent=False)
        if kind == 'task':
            record._upsert_webhook_task(payload)
        record._resolve_partners()
        # See _upsert_from_mcp: never leak sembly_sync to the caller.
        return record.with_context(sembly_sync=False)

    def _queue_delivery_pipeline(self, require_recent=True):
        """A meeting that just arrived enters the match->brief->post pipeline.

        Called from BOTH channels, because either one can be the first — and
        often the only — sight of a meeting. A meeting Sembly's automation
        never pushed (out of the rule's scope, or the automation was off) is
        seen only by the MCP sync, and before this it could never be posted at
        all: its one route was the hourly summary sweep, which ships disabled.

        NOT run inline: the webhook must answer within Sembly's 10s timeout and
        this costs two LLM round trips. The queue cron picks it up within
        seconds instead.

        Only when there is something to say (a summary) and nothing said yet,
        so the second channel arriving for the same meeting is a no-op rather
        than a second note. Matching is skipped for a record a human already
        linked, but the posting still happens — that is the whole point of a
        hand-linked meeting.
        """
        self.ensure_one()
        if not self.has_summary or self.summary_posted or self.ai_match_queued:
            return False
        # The recency guard belongs to the MCP side ONLY, and there it is load
        # bearing: the historical backfill upserts through _upsert_from_mcp,
        # so without it a 2 000-meeting backfill would queue 2 000 LLM matches
        # and post into years of old records.
        #
        # A webhook arrival needs no such test — Sembly pushes a meeting once,
        # when it finishes processing it, so the push IS the freshness signal.
        # Applying the window there instead LOSES notes: a processing delay
        # over the weekend, or a human pressing Sembly's "Zap" to re-send,
        # would be dropped silently.
        if require_recent and not self.filtered_domain(self._recent_window_domain()):
            return False
        self.sudo().with_context(sembly_sync=True).write({'ai_match_queued': True})
        cron = self.env.ref('era_sembly_meetings.cron_sembly_ai_match_queue',
                            raise_if_not_found=False)
        if cron:
            cron.sudo()._trigger()
        return True

    def _store_webhook_payload(self, payload):
        """Keep the webhook's own shape, minus its bulk.

        Sembly does not publish which hooks an automation was configured with,
        and its field names are configurable, so "what did they actually send"
        is otherwise unanswerable: when meeting_link arrived carrying the same
        value our template builds, there was no way to tell whether the field
        was sent at all. Keeping the envelope answers that once and for all.

        The two giant fields are replaced by a length marker rather than
        stored twice — they already live in their own columns, and a 200k-char
        transcript has no business being duplicated here. What remains is
        manager-only (``raw_payload`` is in CONTENT_FIELDS) and the existing
        purge cron clears it after ``sembly.raw_retention_days``.
        """
        self.ensure_one()
        skeleton = {
            key: ('<%s chars>' % len(str(value)))
            if key in ('meeting_transcription', 'meeting_notes') and value
            else value
            for key, value in payload.items()
        }
        self.sudo().with_context(sembly_sync=True).write({
            'raw_payload': json.dumps(skeleton, ensure_ascii=False)[:200000],
        })

    def _upsert_webhook_task(self, payload):
        """One task item per Task-automation POST, keyed on Sembly's item id."""
        self.ensure_one()
        title = (payload.get('item_header_text') or payload.get('item_text')
                 or payload.get('title') or '').strip()
        if not title:
            return
        item_id = payload.get('item_id')
        Item = self.env['sembly.meeting.item'].sudo()
        existing = Item.search([
            ('meeting_id', '=', self.id), ('sembly_item_id', '=', str(item_id)),
        ], limit=1) if item_id else Item.browse()
        values = {
            'meeting_id': self.id,
            'item_type': 'task',
            'name': title[:500],
            'description': payload.get('item_text') or payload.get('description') or False,
            'assigned_to': payload.get('item_assignee') or False,
            'sembly_item_id': str(item_id) if item_id else False,
            'item_url': payload.get('item_link') or False,
        }
        if existing:
            existing.write(values)
        else:
            Item.create(values)

    # ================================================================== write guard
    @api.model
    def _sembly_link_fields(self):
        """SEAM — the fields whose hand edit permanently claims the record.

        Each satellite module adds its own::

            def _sembly_link_fields(self):
                return super()._sembly_link_fields() | {'lead_id'}

        Empty here, because this module contributes no link target of its own.
        """
        return set()

    final_summary = fields.Html(
        string="الملخص", compute='_compute_final_summary',
        help="The ONE text that represents this meeting: the merge when both "
             "providers contributed, otherwise whichever single provider did. "
             "Every original stays available behind its own button.")
    final_summary_label = fields.Char(
        string="مصدر الملخص", compute='_compute_final_summary')

    @api.depends('summary', 'ai_brief')
    def _compute_final_summary(self):
        """One text, chosen — never a pile of sections to read through.

        Reads the same seam everything else does, so a provider widens this by
        implementing _narrative_sources and _narrative_html and nothing more. It
        re-declares its own depends there, exactly as _compute_has_content does,
        because the base can only depend on its own fields.
        """
        for record in self:
            sources = record._narrative_sources()
            if sources:
                label, _body = sources[0]
                record.final_summary_label = label
                record.final_summary = record._narrative_html(label)
            else:
                record.final_summary_label = False
                record.final_summary = False

    def _narrative_html(self, label):
        """SEAM — the rich version of a narrative source, for display.

        _narrative_sources returns PLAIN text because that is what a prompt
        wants; the form wants the markup back.
        """
        self.ensure_one()
        return self.summary or False

    def _narrative_sources(self):
        """SEAM — every prose account of this meeting, labelled by provider.

        Returns ``[(label, text), ...]``. The base contributes Sembly's summary;
        a provider module adds its own. Everything that summarises a meeting —
        has_summary, the executive brief, the chatter note — reads THIS rather
        than the ``summary`` field, so a new provider is included everywhere by
        implementing one method.
        """
        self.ensure_one()
        from odoo.tools import html2plaintext
        text = html2plaintext(self.summary or '').strip()
        return [("Sembly", text)] if text else []

    @api.model
    def _sembly_content_fields(self):
        """SEAM — what an employee may READ but never CHANGE.

        A method rather than the bare constant so a provider module can add its
        own content fields: era_sembly_meetings_google contributes the Drive
        ids and the Gemini notes, which are just as much "what the meeting
        says" as the summary is. Still a DENY list, so a new LINK field stays
        employee-writable without anyone registering it.
        """
        return set(CONTENT_FIELDS)

    def _check_content_access(self, values):
        """An employee files meetings; only a manager rewrites them.

        Enforced SERVER-SIDE (rule 19) rather than by making the fields
        readonly in the form, because a readonly attribute is a rendering hint
        that any RPC client can ignore.

        ``sudo`` is exempt: the two Sembly channels, the matcher and the crons
        all run elevated, and they are precisely the things that are *supposed*
        to write the content.
        """
        if self.env.su or not values:
            return
        if self.env.user.has_group('era_sembly_meetings.group_sembly_manager'):
            return
        forbidden = self._sembly_content_fields() & set(values)
        if forbidden:
            raise AccessError(_(
                "Only a Sembly manager can change what a meeting says. You can "
                "link this meeting to a record, but not edit: %s",
                ", ".join(sorted(forbidden))))

    def write(self, values):
        """A hand edit of a link field permanently claims the record.

        Requirement 3 says the user must be able to choose the opportunity/task
        manually "in all cases"; that is only true if the matcher then leaves
        the record alone. Sync/AI writes pass ``sembly_sync`` in the context and
        are exempt.
        """
        self._check_content_access(values)
        if self._sembly_link_fields() & set(values) and \
                not self.env.context.get('sembly_sync'):
            values = dict(values, link_state='manual')
        return super().write(values)

    # ================================================================== AI matching
    def _ai_candidate_pools(self):
        """SEAM — every linkable model contributes one candidate pool here.

        A pool is a dict::

            {'key': 'lead_id',            # the m2o field on sembly.meeting
             'label': "CANDIDATE OPPORTUNITIES (id | name | customer | stage)",
             'records': <recordset>,      # ALSO the whitelist: see _ai_match_one
             'render': lambda rec: "%s | %s" % (rec.id, rec.name),
             'sequence': 10,              # prompt block order, ties broken by key
             'basis': "leads:participants(2)"}   # optional, for ai_matched_on

        ``records`` is doubly load-bearing: it is what the model is shown AND
        the set it is allowed to choose from, so a pool can never let the model
        link to something it never saw.

        Pools are the ONLY way a link target enters the matcher. This module
        returns none — ``era_sembly_meetings_crm`` / ``_tasks`` / ``_tickets``
        each append one (or two), which is what keeps crm, project and helpdesk
        out of this module's dependencies.
        """
        self.ensure_one()
        return []

    def _title_tokens(self):
        """Significant words of the title — the shared narrowing signal.

        Lives here rather than in a satellite because all of them narrow their
        candidates the same way, and they must do it identically.
        """
        self.ensure_one()
        raw = re.split(r'[^\w؀-ۿ]+', (self.name or ''))
        return [t for t in raw if len(t) >= 3 and t.lower() not in STOP_WORDS]

    def _candidate_partners(self):
        """The participants and their commercial parents.

        The other half of the shared narrowing signal: a meeting is usually
        about a record belonging to somebody who attended it.
        """
        self.ensure_one()
        partners = self.partner_ids
        return partners.mapped('commercial_partner_id') | partners if partners else partners

    def _collect_candidates(self):
        """Deterministic narrowing — NO LLM call here.

        With 2 000+ leads the whole set can never be handed to a model, so each
        pool narrows and hard-caps itself and reports what it dropped. This
        method only gathers the pools and puts them in a stable order, so the
        prompt does not change shape with the module install order.
        """
        self.ensure_one()
        pools = sorted(self._ai_candidate_pools(),
                       key=lambda pool: (pool.get('sequence', 100), pool['key']))
        # Each pool reports the same shared narrowing signals — participants(N)
        # and the title tokens — so joining them verbatim repeats the token list
        # once per pool and blows past ai_matched_on's 255 chars, truncating
        # mid-word exactly where the TRUNCATED marker would have been. Fold the
        # repeats: a signal is named once, with the pools it applied to.
        seen = {}
        for pool in pools:
            for signal in (pool.get('basis') or '').split(','):
                signal = signal.strip()
                if signal:
                    seen.setdefault(signal, []).append(pool['key'].replace('_id', ''))
        basis = "; ".join(
            "%s[%s]" % (signal, ",".join(keys)) for signal, keys in seen.items())
        return pools, basis

    def _build_match_prompt(self, pools):
        """Compose the single LLM prompt.

        DATA MINIMISATION: this method has no access to ``self.transcript`` and
        must never gain one. Only title, date, participant names and a
        truncated summary leave the instance (PDPL).
        """
        self.ensure_one()
        from odoo.tools import html2plaintext

        summary_text = html2plaintext(self.summary or '')[:SUMMARY_PROMPT_CHARS]
        participants = (self.participant_names or '').replace('\n', ', ')

        blocks, keys = [], []
        for pool in pools:
            rendered = "\n".join(pool['render'](rec) for rec in pool['records']) or '(none)'
            blocks.append("%s\n%s" % (pool['label'], rendered))
            keys.append('"%s": <id or null>' % pool['key'])

        return (
            "You link a recorded business meeting to the ONE Odoo record it is about.\n\n"
            "MEETING\n"
            "Title: %s\n"
            "Date: %s\n"
            "Participants: %s\n"
            "Summary (truncated):\n\"\"\"\n%s\n\"\"\"\n\n"
            "%s\n\n"
            "Rules:\n"
            "- You may ONLY return ids that appear in the lists above. Never invent an id.\n"
            "- Return null for any field you are not confident about.\n"
            "- Prefer the most specific match: a task beats a project when the meeting is "
            "clearly about that task.\n"
            "- If a task is chosen, also return its project id.\n"
            "- If nothing matches, return nulls with a low confidence.\n"
            "Reply with ONLY a JSON object, no prose, no code fence:\n"
            '{%s, "confidence": <number 0.0-1.0>, "reason": "<one short sentence>"}'
        ) % (self.name or '-',
             fields.Datetime.to_string(self.started_at) if self.started_at else '-',
             participants or '-', summary_text or '(no summary available)',
             "\n\n".join(blocks) or '(no candidate records available)',
             ", ".join(keys))

    @api.model
    def _ask_agent(self, prompt):
        """Route through the configured ai.agent, exactly as era_domain_industry does."""
        agent_id = self._icp_int('sembly.ai_agent_id', DEFAULT_AGENT_ID)
        agent = self.env['ai.agent'].sudo().browse(agent_id).exists()
        if not agent:
            raise UserError(_(
                "No Sembly AI agent is configured, or the one that was "
                "configured has been deleted. Choose one in Settings → Sembly."))
        response = agent.get_direct_response(prompt)
        if isinstance(response, (list, tuple)):
            # str() every part rather than assuming they are all strings.
            # get_direct_response can return a list carrying a non-string —
            # observed 16 times in two days as "sequence item 0: expected str
            # instance, bool found", which killed the whole match for that
            # meeting. Coercing costs nothing and cannot be wrong: whatever the
            # provider put there, its text is what we want.
            return "\n".join(str(m) for m in response if m not in (None, False, ''))
        if response in (None, False):
            return ''
        return response if isinstance(response, str) else str(response)

    @api.model
    def _extract_json(self, text):
        if not text:
            return None
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if not match:
            return None
        try:
            return json.loads(match.group(0))
        except (ValueError, TypeError):
            return None

    def _ai_match(self, force=False):
        """Propose a link for each meeting in ``self``.

        Two things are load bearing when the provider misbehaves:

        * every attempt is STAMPED, win or lose, so ``_cron_ai_match_batch``
          can send repeat failures to the BACK of the queue instead of
          grinding the head of the list forever;
        * a run of consecutive failures ABORTS the batch. Consecutive failures
          mean the provider is down, not that these meetings are unmatchable,
          and continuing only multiplies identical log rows and burns quota.
          "Selected model is at capacity" did exactly this: the same 20 newest
          unlinked meetings retried every 15 minutes while 1 900 behind them
          never got a turn.
        """
        threshold = self._icp_float('sembly.ai_confidence_threshold', 0.7)
        streak_limit = self._icp_int('sembly.ai_failure_streak', 3)
        streak = 0
        for rec in self:
            if rec.link_state == 'manual' and not force:
                continue  # a human decided; never overwrite it
            try:
                rec._ai_match_one(threshold)
                streak = 0
            except Exception as exc:  # noqa: BLE001 - one bad meeting must not kill a batch
                streak += 1
                _logger.warning("Sembly AI match failed for %s: %s", rec.sembly_meeting_id, exc)
                self.env['sembly.sync.log']._log(
                    'ai', 'match', 'error',
                    "meeting %s: %s" % (rec.sembly_meeting_id, exc))
            finally:
                rec.sudo().with_context(sembly_sync=True).write(
                    {'ai_last_attempt': fields.Datetime.now()})
            if streak >= streak_limit:
                self.env['sembly.sync.log']._log(
                    'ai', 'match', 'error',
                    "%s failures in a row - the AI provider looks unavailable, "
                    "so this batch was abandoned instead of being retried "
                    "against every remaining meeting." % streak)
                break

    def _ai_match_one(self, threshold):
        self.ensure_one()
        unclaimed = False
        pools, basis = self._collect_candidates()
        if not any(pool['records'] for pool in pools):
            # Either nothing matched the narrowing, or no satellite module is
            # installed at all. Both are a no-op, and neither is worth an LLM
            # call.
            self.sudo().with_context(sembly_sync=True).write({
                'ai_matched_on': basis[:255] or 'no candidates',
                'ai_reasoning': "No candidate record could be found.",
            })
            # No candidates at all is the commonest shape of an internal
            # meeting, so this path needs the fallback just as much.
            if self._apply_internal_fallback():
                self._ai_after_link()
            return

        raw = self._ask_agent(self._build_match_prompt(pools))
        data = self._extract_json(raw) or {}

        # The model may only choose from what it was actually shown. Anything
        # else is a hallucinated id and is discarded.
        def pick(key, allowed):
            value = data.get(key)
            try:
                value = int(value)
            except (TypeError, ValueError):
                return False
            return value if value in allowed.ids else False

        links = {pool['key']: pick(pool['key'], pool['records']) for pool in pools}
        links = self._ai_postprocess_links(links, pools)

        try:
            confidence = float(data.get('confidence') or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        reason = (data.get('reason') or '')[:2000]

        values = {
            'ai_confidence': confidence,
            'ai_reasoning': reason,
            'ai_matched_on': basis[:255],
        }
        if not any(links.values()):
            values['link_state'] = 'unlinked'
            unclaimed = True
        elif confidence >= threshold:
            values.update(links)
            values['link_state'] = 'auto'
        else:
            # Below the bar: show the suggestion, link nothing.
            values['link_state'] = 'suggested'
        self.sudo().with_context(sembly_sync=True).write(values)

        # Nothing in the system claimed it: it may simply be an internal team
        # meeting, which has no opportunity or ticket by definition.
        if unclaimed and self._apply_internal_fallback():
            self._ai_after_link()
            return

        if values.get('link_state') == 'auto':
            self._ai_after_link()

    # ============================================================== internal
    @api.model
    def _sembly_link_field_by_model(self):
        """SEAM — ``{model name: link field}``, contributed by the satellites.

        Two jobs at once: it tells the base which field to write when filing a
        meeting on the configured fallback record, and it is what the settings
        picker offers. The base still names no external model.
        """
        return {}

    @api.model
    def _internal_domains(self):
        """The domains that count as OURS.

        Derived from the company and its internal users rather than configured,
        because a hand-maintained list silently rots — a new company domain
        would start reading as an external customer. ``sembly.internal_domains``
        is there for the cases derivation cannot see (a subsidiary's domain).
        """
        domains = set()
        for source in (self.env.company.email,
                       self.env.company.partner_id.email_normalized,
                       self.env.company.website):
            for match in EMAIL_DOMAIN_RE.finditer(source or ''):
                domains.add(match.group(1).lower())
        # Through the ORM, not raw SQL: a login written earlier in this same
        # transaction has not necessarily been flushed, and the SQL would then
        # read a stale row — the domain would look external for the rest of the
        # request.
        # Inactive users included on purpose: a departed colleague's address is
        # still OUR domain, and dropping it would make their old meetings read
        # as external. share=False is the real filter — portal and public users
        # are customers by definition.
        for user in self.env['res.users'].sudo().with_context(active_test=False)\
                .search_read([('share', '=', False)], ['login']):
            _local, _at, domain = (user['login'] or '').partition('@')
            if domain:
                domains.add(domain.lower())
        extra = self._icp('sembly.internal_domains') or ''
        domains.update(d.strip().lower() for d in re.split(r'[,;\s]+', extra) if d.strip())
        return {d for d in domains if d and d not in FREE_EMAIL_DOMAINS}

    @api.model
    def _ignored_domains(self):
        """Domains that say NOTHING either way, like the public providers.

        The Sembly workspace account is the reason this exists: it attends
        every meeting and its address is very often on neither our domain nor a
        public one. On this instance it appeared in 100% of the meetings
        carrying any address at all, against 50% for the company domain — so
        without this, every single meeting reads as "external" and the internal
        fallback can never fire once.
        """
        raw = self._icp('sembly.ignored_domains') or ''
        return {d.strip().lower().lstrip('@')
                for d in re.split(r'[,;\s]+', raw) if d.strip()}

    def _external_attendee_domains(self):
        """Attendee domains that are neither ours, nor public, nor ignored."""
        self.ensure_one()
        neutral = FREE_EMAIL_DOMAINS | self._internal_domains() | self._ignored_domains()
        haystack = "%s %s %s" % (self.participant_emails or '',
                                 self.owner_email or '',
                                 " ".join(self.partner_ids.mapped('email') or []))
        found = {m.group(1).lower() for m in EMAIL_DOMAIN_RE.finditer(haystack)}
        return found - neutral

    def _internal_fallback_record(self):
        """The configured record, resolved generically from "model,id"."""
        ref = self._icp('sembly.internal_fallback_ref') or ''
        model, _sep, res_id = ref.partition(',')
        if not (model and res_id) or model not in self._sembly_link_field_by_model():
            return None
        try:
            record = self.env[model].sudo().browse(int(res_id)).exists()
        except (KeyError, ValueError):
            return None
        return record or None

    def _looks_internal(self):
        """(is_internal, why) for a meeting nothing else claimed.

        The domain test runs FIRST because it is free, deterministic and can
        only ever say NO: one attendee from a real external domain settles it
        without an LLM call. Public providers are ignored on both sides — our
        own staff use them too, so counting them as external would veto real
        internal meetings.

        Absence of emails is inconclusive, not a veto: MCP carries participant
        NAMES only, so a meeting seen by that channel alone has nothing to
        test. The model's judgement is the primary condition either way.
        """
        self.ensure_one()
        external = self._external_attendee_domains()
        if external:
            return False, "external attendee domain(s): %s" % ", ".join(sorted(external))

        raw = self._ask_agent(self._build_internal_prompt())
        data = self._extract_json(raw) or {}
        verdict = bool(data.get('internal'))
        reason = (data.get('reason') or '')[:500]
        return verdict, reason

    def _build_internal_prompt(self):
        """Same data minimisation as the matcher: no transcript, ever."""
        self.ensure_one()
        from odoo.tools import html2plaintext
        return (
            "Decide whether this meeting is an INTERNAL team meeting or is "
            "about an EXTERNAL customer, prospect, supplier or partner.\n"
            "Internal means the company's own staff discussing their own work: "
            "stand-ups, planning, reviews, one-to-ones, recruitment, internal "
            "training or tooling.\n"
            "If the meeting is with, about, or in preparation for a specific "
            "outside party, it is NOT internal.\n"
            'Answer with JSON only: {"internal": true|false, "reason": "one short sentence"}\n\n'
            "Title: %s\nDate: %s\nParticipants: %s\n\nSummary:\n%s"
        ) % (self.name or '-',
             fields.Datetime.to_string(self.started_at) if self.started_at else '-',
             (self.participant_names or '-').replace('\n', ', '),
             html2plaintext(self.summary or '')[:SUMMARY_PROMPT_CHARS] or '(none)')

    def _apply_internal_fallback(self):
        """File an unclaimed, internal-looking meeting on the configured record.

        Costs nothing when the setting is empty — which is the default — so no
        instance pays an LLM call for a feature it has not turned on.
        """
        self.ensure_one()
        target = self._internal_fallback_record()
        if not target:
            return False
        field = self._sembly_link_field_by_model().get(target._name)
        if not field or self[field]:
            return False

        internal, why = self._looks_internal()
        if not internal:
            return False
        self.sudo().with_context(sembly_sync=True).write({
            field: target.id,
            'link_state': 'auto',
            'ai_reasoning': "Filed as an internal meeting: %s" % (why or "no external attendees"),
        })
        return True

    def _ai_postprocess_links(self, links, pools):
        """SEAM — derive one link from another before anything is written.

        ``era_sembly_meetings_tasks`` uses it to fill in the project of a
        chosen task, so the model returning only ``task_id`` still produces a
        complete link. Return the (possibly modified) ``links`` dict.
        """
        self.ensure_one()
        return links

    def _ai_after_link(self):
        """SEAM — side effects of a successful AUTOMATIC link.

        Runs only for ``link_state == 'auto'``: a manual link is the user's
        business, and a below-threshold suggestion has linked nothing yet.
        ``era_sembly_meetings_tasks`` uses it for the per-project "الاجتماعات"
        bucket task.
        """
        self.ensure_one()
        return True

    def _has_external_link(self):
        """SEAM — "some other module has already claimed this meeting".

        ``era_sembly_meetings_crm`` returns True for a linked opportunity and
        ``_tickets`` for a linked ticket, so that ``_tasks`` does not file a
        meeting under a project bucket task on top of a more specific link.
        """
        self.ensure_one()
        return False

    # ================================================================== brief
    def _build_brief_prompt(self):
        """The condensation prompt.

        DATA MINIMISATION: like the matcher, this has no access to
        ``self.transcript`` and must never gain one. The summary and the
        structured items already left the instance for matching; nothing new
        is exposed here.
        """
        self.ensure_one()
        from odoo.tools import html2plaintext

        # Every provider's account, not just Sembly's: the whole point of a
        # second provider is that it saw what the first one missed.
        sources = self._narrative_sources()
        summary_text = "\n\n".join(
            "=== %s ===\n%s" % (label, body[:BRIEF_PROMPT_CHARS])
            for label, body in sources)
        item_lines = []
        for item in self.item_ids:
            if item.item_type in ('decision', 'issue', 'risk', 'requirement', 'task'):
                item_lines.append("- [%s] %s" % (item.item_type, item.name))
        participants = (self.participant_names or '').replace('\n', ', ')

        return (
            "أنت مساعد يلخّص محاضر الاجتماعات لفريق المبيعات والتنفيذ.\n"
            "اكتب ملخصاً تنفيذياً موجزاً بالعربية لهذا الاجتماع، ليُنشر في سجل متابعة "
            "العميل، بهذه الأقسام حصراً وبهذا الترتيب، بصيغة HTML بسيطة "
            "(<h4> للعناوين و<ul><li> للنقاط):\n"
            "<h4>حالة العميل</h4> سطر أو سطران يصفان وضعه الحالي.\n"
            "<h4>الإيجابيات</h4>\n"
            "<h4>السلبيات والمخاطر</h4>\n"
            "<h4>نطاق العمل</h4>\n"
            "<h4>الخطوة القادمة</h4> نقطة واحدة واضحة، بالمسؤول والموعد إن ذُكرا.\n"
            "التزم بما ورد في المحضر فقط ولا تخترع شيئاً؛ إن غاب قسم فاكتب "
            "\"غير مذكور\". لا مقدمات ولا خاتمة ولا أقسام أخرى، وأجب بالـ HTML فقط.\n\n"
            "عنوان الاجتماع: %s\n"
            "التاريخ: %s\n"
            "المشاركون: %s\n\n"
            "الملخص:\n%s\n\n"
            "العناصر المستخرجة:\n%s"
        ) % (self.name or '-',
             fields.Datetime.to_string(self.started_at) if self.started_at else '-',
             participants or '-',
             summary_text or '(لا يوجد)',
             "\n".join(item_lines[:30]) or '(لا يوجد)')

    def _ensure_ai_brief(self):
        """Generate the brief once; NEVER let it block the posting.

        Any failure — agent gone, provider down, unusable reply — is logged
        and the caller falls back to the raw summary. Toggled by
        ``sembly.ai_brief_enabled`` so the behaviour can be switched off
        without an upgrade.
        """
        self.ensure_one()
        if self.ai_brief:
            return self.ai_brief
        if self._icp('sembly.ai_brief_enabled', '1') not in ('1', 'True', 'true'):
            return ''
        if not self._narrative_sources():
            return ''
        try:
            raw = self._ask_agent(self._build_brief_prompt())
        except Exception as exc:  # noqa: BLE001 - the note must still go out
            self.env['sembly.sync.log']._log(
                'ai', 'brief', 'error',
                "meeting %s: %s" % (self.sembly_meeting_id, exc))
            return ''
        brief = self._coerce_html(raw)
        if brief:
            self.sudo().with_context(sembly_sync=True).write({'ai_brief': brief})
        return brief

    # ================================================================== chatter
    def _summary_note_body(self):
        """Render the internal note (QWeb, so it stays translatable)."""
        self.ensure_one()
        return self.env['ir.qweb']._render(
            'era_sembly_meetings.sembly_summary_note',
            {'meeting': self, 'base_url': self._icp('web.base.url', '')},
            raise_if_not_found=False) or ''

    def action_post_summary_to_chatter(self):
        """Manual button — deliberately ignores the today/yesterday window.

        A user asking explicitly is an override, and this is the escape hatch
        for older meetings. It reports back when nothing was posted, because
        the reason (no summary, or no linked record to post it on) is not
        visible on the form.
        """
        posted = sum(1 for rec in self if rec._post_summary(force=True))
        if posted:
            message = _("Summary posted on %s record(s).", posted)
        else:
            message = _("Nothing to post: the meeting has no summary, or it is "
                        "not linked to any record yet.")
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {'title': _("Sembly"), 'message': message,
                       'type': 'success' if posted else 'warning'},
        }

    def _post_summary(self, force=False):
        """Post the summary as an INTERNAL note on the linked records."""
        self.ensure_one()
        if self.summary_posted and not force:
            return False
        if not self._narrative_sources():
            return False
        targets = self._summary_targets()
        if not targets:
            return False

        self._ensure_ai_brief()
        body = self._summary_note_body()
        if not body:
            return False
        author = self.env.ref('base.partner_root', raise_if_not_found=False)
        for target in targets:
            target.sudo().message_post(
                body=body,
                # mail.mt_note = internal note. Meeting content must never be
                # emailed to a lead's customer-side followers.
                subtype_xmlid='mail.mt_note',
                author_id=author.id if author else False,
            )
        self.sudo().with_context(sembly_sync=True).write({
            'summary_posted': True,
            'summary_posted_on': fields.Datetime.now(),
        })
        return True

    def _summary_targets(self):
        """SEAM — which records receive the note.

        Every satellite appends its own, so a meeting linked to both an
        opportunity and a task is noted on both::

            def _summary_targets(self):
                targets = super()._summary_targets()
                if self.lead_id:
                    targets.append(self.lead_id)
                return targets

        Empty here: with no satellite installed there is nothing to post on,
        and ``_post_summary`` then does nothing.
        """
        self.ensure_one()
        return []

    @api.model
    def _recent_window_domain(self):
        """started_at falling on today or yesterday IN THE COMPANY TIMEZONE.

        Computed by converting the local day boundaries to UTC, not by naive
        UTC arithmetic — a 21:00 Riyadh meeting is 18:00 UTC and must count as
        *today*.
        """
        tz = self._tz()
        now_local = fields.Datetime.now().replace(tzinfo=pytz.UTC).astimezone(tz)
        start_local = tz.localize(
            datetime.combine(now_local.date() - timedelta(days=1), datetime.min.time()))
        end_local = tz.localize(
            datetime.combine(now_local.date(), datetime.max.time().replace(microsecond=0)))
        return [
            ('started_at', '>=', start_local.astimezone(pytz.UTC).replace(tzinfo=None)),
            ('started_at', '<=', end_local.astimezone(pytz.UTC).replace(tzinfo=None)),
        ]

    # ------------------------------------------------------------- originals
    def _open_text_dialog(self, title, body, note=False, link=False):
        """Show one piece of text in a modal.

        Goes through a transient of its own: an act_window whose res_model and
        res_id are the record already on screen is NOT honoured as a dialog —
        Odoo replaces the page with it, menu bar and all. That was the bug.
        """
        self.ensure_one()
        dialog = self.env['sembly.text.dialog'].create({
            'meeting_id': self.id,
            'title': title,
            'note': note,
            'link': link,
            'body': body or '<p class="text-muted">لا يوجد نص.</p>',
        })
        return {
            'type': 'ir.actions.act_window',
            'name': title,
            'res_model': 'sembly.text.dialog',
            'res_id': dialog.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def action_show_sembly_text(self):
        self.ensure_one()
        return self._open_text_dialog(
            _("ملخص Sembly الأصلي"), self.summary,
            note=_("النص كما أرسلته Sembly، قبل أي دمج أو ترجمة."))

    def action_show_brief_text(self):
        self.ensure_one()
        return self._open_text_dialog(
            _("مختصر الشاتر"), self.ai_brief,
            note=_("هذا ما يُنشر في شاتر الفرصة/المهمة/التذكرة. يُبنى من الملخص "
                   "المدموج حين يتوفّر مصدران، ومن المتوفّر وحده حين لا يتوفّر إلا واحد."))

    # ================================================================== actions
    def action_refresh_from_sembly(self):
        """Re-fetch this meeting's details from MCP on demand."""
        self.ensure_one()
        client = self._get_client()
        try:
            details = client.get_meeting(int(self.sembly_meeting_id))
        except (SemblyMcpError, ValueError) as exc:
            raise UserError(_("Sembly refresh failed: %s", exc)) from exc
        if not details:
            raise UserError(_("Sembly returned no details for meeting %s.",
                              self.sembly_meeting_id))
        self._upsert_from_mcp(details.get('metadata') or {'id': self.sembly_meeting_id},
                              details)
        return True

    def action_ai_match(self):
        """Button — forces a re-match even on a manually linked record."""
        self._ai_match(force=True)
        return True

    def action_queue_ai_match(self):
        """The list's bulk action: QUEUE the selection, never loop over it.

        Every match is one LLM round trip of a few seconds, so looping inline
        over a big selection would blow the HTTP worker's 240s limit exactly
        like the historical import used to. Queuing also changes the force
        semantics deliberately: the form button on ONE record may override a
        human decision, but a bulk sweep must never — manually linked rows are
        reported back, not re-matched.
        """
        manual = self.filtered(lambda meeting: meeting.link_state == 'manual')
        todo = self - manual
        if todo:
            todo.sudo().with_context(sembly_sync=True).write({'ai_match_queued': True})
            cron = self.env.ref('era_sembly_meetings.cron_sembly_ai_match_queue',
                                raise_if_not_found=False)
            if cron:
                cron.sudo()._trigger()

        if todo and manual:
            message = _("%(todo)s meeting(s) queued for AI matching. %(manual)s "
                        "manually linked meeting(s) were left untouched — use the "
                        "button on the form to re-match one deliberately.",
                        todo=len(todo), manual=len(manual))
        elif todo:
            message = _("%s meeting(s) queued for AI matching. Results arrive "
                        "within a few minutes.", len(todo))
        else:
            message = _("Nothing to match: every selected meeting is manually "
                        "linked, and a bulk action never overrides a human decision.")
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {'title': _("Sembly"), 'message': message,
                       'type': 'success' if todo else 'warning'},
        }

    @api.model
    def _cron_ai_match_queue(self):
        """Drain the bulk-action queue within a wall-clock budget.

        The flag is cleared BEFORE the match on purpose: a record whose match
        crashes loses its request instead of wedging the queue forever. And
        ``_ai_match`` re-checks ``manual`` at run time, so a record hand-linked
        between queuing and processing is still left alone.
        """
        budget = self._icp_int('sembly.match_seconds', 60)
        deadline = time.monotonic() + budget
        processed = 0
        while True:
            record = self.search([('ai_match_queued', '=', True)],
                                  order='id', limit=1)
            if not record:
                break
            record.sudo().with_context(sembly_sync=True).write(
                {'ai_match_queued': False})
            if record.link_state != 'manual':
                record._ai_match()
            # Post as soon as it HAS a home. Only for meetings inside the
            # recent window, so a bulk re-match of two years of history cannot
            # dump a note into hundreds of old opportunities; a webhook arrival
            # is always inside it.
            if record.filtered_domain(self._recent_window_domain()):
                record._post_summary()
            processed += 1
            if self._may_commit():
                self.env.cr.commit()
            if time.monotonic() >= deadline:
                break

        remaining = self.search_count([('ai_match_queued', '=', True)])
        if remaining:
            # Budget ran out mid-queue: continue in the next cron slot right
            # away rather than waiting for the hourly sweep.
            cron = self.env.ref('era_sembly_meetings.cron_sembly_ai_match_queue',
                                raise_if_not_found=False)
            if cron:
                cron.sudo()._trigger()
        if processed:
            self.env['sembly.sync.log']._log(
                'ai', 'match-queue', 'ok',
                "%s matched, %s still queued" % (processed, remaining),
                meeting_count=processed)
        return True

    def action_apply_ai_suggestion(self):
        """Promote a below-threshold suggestion to an actual link.

        Re-runs the matcher with the threshold effectively removed for this one
        record, so the ids are re-validated against a fresh candidate set rather
        than trusted from a stale reasoning string.
        """
        for rec in self:
            rec._ai_match_one(threshold=0.0)
        return True

    def action_open_sembly(self):
        """Open the meeting in Sembly, preferring the GUEST link.

        Not everyone here has a Sembly account, and the workspace link asks for
        one, so the guest link is the one that actually works for most of the
        team.
        """
        self.ensure_one()
        url = (self.share_url or self.meeting_url
               or self._build_meeting_url(self.sembly_meeting_id))
        return {'type': 'ir.actions.act_url', 'url': url, 'target': 'new'}

    def _action_open_link(self, field_name):
        """Shared implementation of every satellite's "open the linked record"
        smart button, so three modules do not each hand-roll the same dict."""
        self.ensure_one()
        record = self[field_name]
        return {
            'type': 'ir.actions.act_window', 'res_model': record._name,
            'res_id': record.id, 'view_mode': 'form',
        }

    # ================================================================== crons
    @api.model
    def _cron_sync_meetings(self):
        """Pull the rolling window from MCP. Never raises — cron must survive."""
        Log = self.env['sembly.sync.log']
        started = datetime.utcnow()
        client = self._get_client()
        if not client.token:
            Log._log('mcp', 'sync', 'error',
                     "No Sembly MCP token configured (SEMBLY_MCP_TOKEN env var or "
                     "the sembly.mcp_token parameter). Sync skipped.")
            return False

        window = self._icp_int('sembly.sync_window_days', 7)
        batch = self._icp_int('sembly.sync_batch_size', 50)
        from_date = (date.today() - timedelta(days=window)).isoformat()

        try:
            meetings = client.list_meetings(from_date=from_date, limit=200)
        except SemblyMcpError as exc:
            Log._log('mcp', 'list_meetings', 'error', str(exc))
            return False

        detailed = 0
        for meta in meetings:
            try:
                record = self._find_by_sembly_id(meta.get('id'))
                needs_details = (
                    not record
                    or not record.has_summary
                    or record.finished_at != self._parse_dt(meta.get('finished_at'))
                )
                details = None
                if needs_details and detailed < batch:
                    details = client.get_meeting(meta['id'])
                    detailed += 1
                self._upsert_from_mcp(meta, details)
                # Commit per meeting so one bad payload cannot lose a whole run.
                if self._may_commit():
                    self.env.cr.commit()
            except Exception as exc:  # noqa: BLE001 - keep going, log, next meeting
                _logger.warning("Sembly sync failed for meeting %s: %s", meta.get('id'), exc)
                Log._log('mcp', 'get_meeting', 'error',
                         "meeting %s: %s" % (meta.get('id'), exc))

        Log._log('mcp', 'sync', 'ok',
                 "window=%sd listed=%s detailed=%s" % (window, len(meetings), detailed),
                 meeting_count=len(meetings),
                 duration_ms=int((datetime.utcnow() - started).total_seconds() * 1000))
        return True

    @api.model
    def _cron_ai_match_batch(self):
        """Match a batch of unlinked meetings, within a wall-clock budget.

        Never-tried meetings first, then a periodic RE-SEARCH of the ones that
        came back unlinked, because the answer genuinely can change — the
        opportunity a meeting belongs to is often created after the meeting.
        The cooldown (``sembly.rematch_after_days``, default 7) is what keeps
        that from becoming a permanent spend; set it to -1 to switch
        re-searching off entirely.

        The batch size alone is not a safety bound: 20 meetings is 20 LLM
        round trips, which can pass the cron worker's 240s limit on a slow
        provider day (the WorkerCron timeouts in the log). Committing per
        meeting also keeps each transaction short, so this cron stops
        colliding with the backfill writing the same rows —
        SerializationFailure at flush time was the other failure mode.
        """
        limit = self._icp_int('sembly.match_batch_size', 20)
        budget = self._icp_int('sembly.match_seconds', 60)
        deadline = time.monotonic() + budget

        # Never-tried meetings first, then the least recently tried. Ordering
        # by started_at alone meant the same 20 newest unlinked meetings were
        # re-attempted every 15 minutes — so a provider outage, or simply a
        # meeting the model can never link, starved the ~1 900 behind them
        # indefinitely. Two searches rather than "NULLS FIRST" so the ordering
        # is explicit and not at the mercy of the backend's null collation.
        meetings = self.search(
            [('link_state', '=', 'unlinked'), ('ai_last_attempt', '=', False)],
            order='started_at desc', limit=limit)
        # Then RE-SEARCH the ones already tried — because the answer can change:
        # the opportunity a meeting belongs to may only have been created after
        # the first attempt. But only after a cooldown. Without one this cron
        # never goes quiet: with ~1 800 unlinked meetings it would recycle the
        # same set every 15 minutes forever, paying an LLM round trip each time
        # to re-derive the same "no match" from unchanged data.
        cooldown = self._icp_int('sembly.rematch_after_days', 7)
        if len(meetings) < limit and cooldown >= 0:
            cutoff = fields.Datetime.now() - timedelta(days=cooldown)
            meetings |= self.search(
                [('link_state', '=', 'unlinked'), ('ai_last_attempt', '!=', False),
                 ('ai_last_attempt', '<', fields.Datetime.to_string(cutoff))],
                order='ai_last_attempt asc', limit=limit - len(meetings))
        for meeting in meetings:
            meeting._ai_match()
            if self._may_commit():
                self.env.cr.commit()
            if time.monotonic() >= deadline:
                break
        return True

    @api.model
    def _cron_post_recent_summaries(self):
        """Requirement 6 — post ONLY the latest qualifying meeting per record."""
        domain = self._recent_window_domain() + [
            ('summary_posted', '=', False),
            ('has_summary', '=', True),
        ]
        candidates = self.search(domain, order='started_at desc')
        candidates = candidates.filtered(lambda m: m._summary_targets())
        if not candidates:
            return True

        posted_for = set()
        for meeting in candidates:  # already newest-first
            keys = [(t._name, t.id) for t in meeting._summary_targets()]
            if any(key in posted_for for key in keys):
                # An older meeting on a record whose latest one was just posted.
                # Mark it done so it is never posted late.
                meeting.sudo().with_context(sembly_sync=True).write({
                    'summary_posted': True,
                    'summary_posted_on': fields.Datetime.now(),
                })
                continue
            if meeting._post_summary():
                posted_for.update(keys)
        return True

    # ================================================================== backfill
    # Importing the whole history cannot be done in a web request. Every meeting
    # costs one get_meeting round trip, so a few hundred of them run for many
    # minutes, and the HTTP worker is killed at limit_time_real (240s here) with
    # nothing to show for it. So the wizard only ARMS the backfill and returns;
    # this cron does the work, walking backwards through history in windows,
    # committing as it goes, and picking up where it left off on the next tick.
    @api.model
    def _start_backfill(self, date_from=False, date_to=False):
        """Arm the background backfill and wake its cron."""
        icp = self.env['ir.config_parameter'].sudo()
        icp.set_param('sembly.backfill_cursor', (date_to or date.today()).isoformat())
        icp.set_param('sembly.backfill_floor',
                      date_from.isoformat() if date_from else '')
        icp.set_param('sembly.backfill_state', 'running')
        icp.set_param('sembly.backfill_empty_streak', '0')
        icp.set_param('sembly.backfill_imported', '0')
        icp.set_param('sembly.backfill_offset', '0')
        cron = self.env.ref('era_sembly_meetings.cron_sembly_backfill',
                            raise_if_not_found=False)
        if cron:
            # Ships inactive like every other network cron: nothing reaches
            # Sembly until a human asks for it. Asking is this.
            cron.sudo().write({'active': True, 'nextcall': fields.Datetime.now()})
        return True

    @api.model
    def _stop_backfill(self, state='done', message=''):
        """Record the verdict. NEVER touch ir_cron from here.

        A cron worker holds ``FOR NO KEY UPDATE`` on its own ir_cron row in a
        SEPARATE transaction from the one the job runs in (see
        ir_cron._process_job), so a job that writes its own row waits on a lock
        its own worker is holding — forever. Odoo's ir_cron.write() raises a
        UserError precisely to stop that, and bypassing it with raw SQL turned a
        loud failure into a wedged transaction that also blocked module installs
        and every other cron operation on the database.

        So: set the parameter, COMMIT it so the verdict survives, and let the
        next tick see state != 'running' and return immediately. The cron stays
        armed but costs one parameter read every five minutes, which is the
        price of not deadlocking. Disarming it for real happens OUTSIDE a cron —
        from the wizard, or by hand in the UI.
        """
        icp = self.env['ir.config_parameter'].sudo()
        icp.set_param('sembly.backfill_state', state)
        if self._may_commit():
            self.env.cr.commit()
        self.env['sembly.sync.log']._log(
            'mcp', 'backfill', 'ok' if state == 'done' else 'error',
            message or "Backfill finished (%s)" % state,
            meeting_count=self._icp_int('sembly.backfill_imported', 0))
        return True

    @api.model
    def _disarm_backfill_cron(self):
        """Deactivate the backfill cron. Safe ONLY outside a cron run.

        Called from the wizard (an HTTP request), never from
        ``_cron_backfill_history`` — see _stop_backfill for why.
        """
        cron = self.env.ref('era_sembly_meetings.cron_sembly_backfill',
                            raise_if_not_found=False)
        if cron and cron.active:
            cron.sudo().write({'active': False})
            return True
        return False

    @api.model
    def _backfill_list_window(self, client, window_start, window_end):
        """List one window, narrowing it until the server cap is not hit.

        ``list_meetings`` is capped at 200 server-side, and a window returning
        exactly 200 is INDISTINGUISHABLE from one that was truncated — accepting
        it silently would skip meetings and the gap would never be noticed. So
        halve the window until it fits; only a single day that still hits the
        cap is genuinely unresolvable, and that is reported rather than hidden.
        """
        days = max(1, (window_end - window_start).days)
        while True:
            listed = client.list_meetings(
                from_date=window_start.isoformat(),
                to_date=window_end.isoformat(), limit=LIST_MEETINGS_CAP)
            if len(listed) < LIST_MEETINGS_CAP or days <= 1:
                return listed, window_start, len(listed) >= LIST_MEETINGS_CAP
            days = max(1, days // 2)
            window_start = window_end - timedelta(days=days)

    @api.model
    def _cron_backfill_history(self):
        """Walk history backwards until it runs dry. Never raises."""
        Log = self.env['sembly.sync.log']
        icp = self.env['ir.config_parameter'].sudo()
        if (icp.get_param('sembly.backfill_state') or 'idle') != 'running':
            # Nothing to do, and deliberately nothing to write: see
            # _stop_backfill on why a cron must not touch its own row.
            return True

        client = self._get_client()
        if not client.token:
            return self._stop_backfill('error', "No Sembly MCP token configured.")

        window_days = self._icp_int('sembly.backfill_window_days', 7)
        budget = self._icp_int('sembly.backfill_seconds', 90)
        max_empty = self._icp_int('sembly.backfill_empty_windows', 6)
        floor_raw = icp.get_param('sembly.backfill_floor') or ''
        floor = date.fromisoformat(floor_raw) if floor_raw else None
        cursor = date.fromisoformat(
            icp.get_param('sembly.backfill_cursor') or date.today().isoformat())
        empty_streak = self._icp_int('sembly.backfill_empty_streak', 0)
        imported_total = self._icp_int('sembly.backfill_imported', 0)

        # Wall-clock budget rather than a fixed batch size: one slow window must
        # not push the run into the cron's own time limit.
        deadline = time.monotonic() + budget
        windows = 0

        # ALWAYS process at least one window per tick, then keep going while the
        # budget lasts. A plain `while` on the clock means a budget of 0 — or a
        # tick that starts already over it — walks nothing at all, and the
        # backfill silently never advances.
        while windows == 0 or time.monotonic() < deadline:
            window_end = cursor
            window_start = window_end - timedelta(days=window_days)
            if floor and window_start < floor:
                window_start = floor

            try:
                listed, window_start, truncated = self._backfill_list_window(
                    client, window_start, window_end)
            except SemblyMcpError as exc:
                Log._log('mcp', 'backfill', 'error',
                         "window %s..%s: %s" % (window_start, window_end, exc))
                # Leave the cursor where it is so the next tick retries this
                # window rather than skipping over it.
                return True

            if truncated:
                Log._log('mcp', 'backfill', 'error',
                         "A SINGLE DAY (%s) returned the %s-meeting cap, so it "
                         "may be truncated and some meetings of that day were "
                         "possibly missed." % (window_end, LIST_MEETINGS_CAP))

            # Resume INSIDE the window. A dense week can hold more meetings
            # than one tick's budget, and re-listing from the window's start
            # every tick means each run re-walks everything it already did
            # before reaching new work — four ticks sat on 2025-06-29..07-06
            # this way. Sembly lists newest-first and that order is stable, so
            # an offset is enough; it is cleared whenever the window moves.
            offset = self._icp_int('sembly.backfill_offset', 0)
            if offset and offset < len(listed):
                listed = listed[offset:]
            elif offset:
                # The window shrank under us (adaptive narrowing, or meetings
                # removed): start it over rather than skip the whole thing.
                offset = 0

            ran_out = False
            for meta in listed:
                try:
                    record = self._find_by_sembly_id(meta.get('id'))
                    details = None
                    if not record or not record.has_summary:
                        details = client.get_meeting(meta['id'])
                    self._upsert_from_mcp(meta, details)
                    imported_total += 1
                    offset += 1
                    # Commit per meeting: a killed run keeps everything it
                    # already fetched, and the next tick does not redo it.
                    if self._may_commit():
                        self.env.cr.commit()
                except Exception as exc:  # noqa: BLE001 - one bad meeting, keep going
                    if 'rate limit' in str(exc).lower():
                        # The client already slept and retried; still limited
                        # means the server wants a real pause. Skipping would
                        # advance the cursor past this meeting FOREVER — a
                        # silent gap — so stop the run instead: the cursor
                        # stays, and the next tick re-lists this window with
                        # everything already imported skipping cheaply.
                        Log._log('mcp', 'backfill', 'ok',
                                 "rate limited at meeting %s; window %s..%s "
                                 "will resume next tick. %s meeting(s) so far."
                                 % (meta.get('id'), window_start, window_end,
                                    imported_total))
                        icp.set_param('sembly.backfill_imported',
                                      str(imported_total))
                        icp.set_param('sembly.backfill_offset', str(offset))
                        if self._may_commit():
                            self.env.cr.commit()
                        return True
                    _logger.warning("Sembly backfill failed for %s: %s",
                                    meta.get('id'), exc)
                    Log._log('mcp', 'backfill', 'error',
                             "meeting %s: %s" % (meta.get('id'), exc))
                # The budget must hold INSIDE a window too: one dense week can
                # carry a hundred get_meeting round trips, and checking only
                # between windows is how runs blew through the cron worker's
                # 240s limit (WorkerCron timeout in the log). Bailing mid-window
                # is cheap — the cursor stays, the next tick re-lists the same
                # window, and every meeting already summarised skips its
                # details fetch.
                if time.monotonic() >= deadline:
                    ran_out = True
                    break
            if ran_out:
                icp.set_param('sembly.backfill_imported', str(imported_total))
                icp.set_param('sembly.backfill_offset', str(offset))
                if self._may_commit():
                    self.env.cr.commit()
                Log._log('mcp', 'backfill', 'ok',
                         "budget ran out inside the %s..%s window; resuming "
                         "there next tick. %s meeting(s) so far."
                         % (window_start, window_end, imported_total),
                         meeting_count=imported_total)
                return True

            empty_streak = 0 if listed else empty_streak + 1
            cursor = window_start - timedelta(days=1)
            windows += 1

            icp.set_param('sembly.backfill_cursor', cursor.isoformat())
            icp.set_param('sembly.backfill_offset', '0')   # window done
            icp.set_param('sembly.backfill_empty_streak', str(empty_streak))
            icp.set_param('sembly.backfill_imported', str(imported_total))
            if self._may_commit():
                self.env.cr.commit()

            if floor and cursor < floor:
                return self._stop_backfill(
                    'done', "Reached %s. Imported %s meeting(s)." % (floor, imported_total))
            if empty_streak >= max_empty:
                return self._stop_backfill(
                    'done', "%s empty windows in a row before %s; history looks "
                            "exhausted. Imported %s meeting(s)."
                            % (empty_streak, cursor, imported_total))

        Log._log('mcp', 'backfill', 'ok',
                 "%s window(s) this run, back to %s. %s meeting(s) so far."
                 % (windows, cursor, imported_total),
                 meeting_count=imported_total)
        return True

    @api.model
    def _cron_purge_raw(self):
        retention = self._icp_int('sembly.raw_retention_days', 30)
        cutoff = fields.Datetime.to_string(datetime.utcnow() - timedelta(days=retention))
        stale = self.search([('raw_payload', '!=', False), ('create_date', '<', cutoff)])
        if stale:
            stale.sudo().write({'raw_payload': False})
        log_cutoff = fields.Datetime.to_string(datetime.utcnow() - timedelta(days=90))
        self.env['sembly.sync.log'].sudo().search(
            [('create_date', '<', log_cutoff)]).unlink()
        return True

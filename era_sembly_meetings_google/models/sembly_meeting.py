# -*- coding: utf-8 -*-
"""Google as a SECOND provider alongside Sembly, on the same meeting record.

The base app already merges two channels (MCP and webhook) onto one
``sembly.meeting`` keyed by the Sembly id. Google is a third channel, and the
only genuinely new problem it brings is that it has **no Sembly id to key on**:
Google knows a Drive file and a start time, Sembly knows its own numeric id.
So the two are matched heuristically — see ``_match_google_artifact``.

WHAT GOOGLE ADDS THAT SEMBLY CANNOT:
  * the actual MP4, and a share link that opens WITHOUT an account,
  * and, when the meeting was held in one of the eight languages Google's
    note-taker supports, a Gemini notes document.

WHAT IT DOES NOT ADD, and must not be expected to:
  * Arabic notes. Google's note-taker supports English, French, German,
    Italian, Japanese, Korean, Portuguese and Spanish, and nothing else, so on
    an Arabic meeting there is simply NO Gemini document to find. The recording
    is language-neutral and still arrives. ``_cron_sync_google`` therefore
    treats "no notes" as the normal case, never as a failure.
"""
import logging
import re
import time
from datetime import datetime, timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools import html_sanitize, plaintext2html

from ..services.google_workspace_client import (
    GoogleWorkspaceClient, GoogleWorkspaceError)

_logger = logging.getLogger(__name__)

# How far apart a Drive recording and a Sembly meeting may start and still be
# the same meeting. A recording begins when someone presses record, which is
# always a little after the meeting itself opens.
DEFAULT_MATCH_MINUTES = 20
# Fallback tolerance when the name has no timestamp: the upload lag measured
# on real data ran to 457 minutes, so anything tighter drops the tail.
DEFAULT_UPLOAD_LAG_MINUTES = 480
# Meet names a recording "<title> - YYYY/MM/DD HH:MM GMT+TZ - Recording", and
# that timestamp is the MEETING START. Drive's createdTime is when the upload
# finished, which is a different thing entirely — measured across 635 real
# recordings: median 75 minutes later, p90 181, max 457, and NEVER earlier. So
# 98% of them fall outside any sane window around createdTime, and the filename
# is the only trustworthy key. The offset is captured too: this workspace mixes
# GMT+03:00 and GMT+04:00, so ignoring it would import a whole hour wrong.
#
# Meet has used SIX spellings of that timestamp over the years, and this
# workspace's Drive holds all of them. Counted over its 3 155 recordings:
#
#     2026/08/12 12:53 GMT+03:00     1 344   the current one
#     (2026-08-12 12:53 GMT+3)         960   dashes, short offset
#     (2024-02-14 12:07 GMT)           131   no offset at all — means UTC
#     (2020-10-21 at 03:03 GMT-7)       53   the oldest, with "at"
#     (2024-03-20 10_11 GMT+3)           1   colon replaced on upload
#     (2023-06-08 19 00 GMT+3)           1   colon dropped altogether
#
# Matching only the first left 1 146 recordings (36% of the workspace) falling
# back to createdTime — a MEDIAN OF 64 MINUTES LATE, mean 89, worst 853 — and
# 89% of them outside the ±20 minute window _adopt_orphan_google_record uses,
# so Sembly could never adopt them and every one of those meetings ended up
# split across two records. Hence: separator-agnostic, "at" optional, offset
# optional.
#
# A BARE "GMT" IS READ AS UTC+0, which is what the label says and what the data
# confirms: taken literally those 131 uploads follow their meeting by 8 minutes
# at the shortest and NEVER precede it, matching the measured lag; read as +3
# the shortest would be 188 minutes, which no recording does.
MEET_NAME_TIME = re.compile(
    r'(\d{4})[/-](\d{2})[/-](\d{2})\s+(?:at\s+)?(\d{1,2})[:_ ](\d{2})'
    r'\s*GMT(?:([+-])(\d{1,2})(?::(\d{2}))?)?', re.IGNORECASE)

# Meet does not always write an offset — sometimes it writes the zone's NAME.
# 277 more of this Drive's recordings are stamped that way, and without these
# they fall back to createdTime exactly as the unparsed spellings did.
#
# The offsets are not looked up in a table of world timezones, which would be
# guessing: AST is Arabia Standard Time (+3) here but Atlantic Standard Time
# (-4) elsewhere. They are MEASURED against the same invariant the rest of this
# file leans on — an upload never precedes its meeting. Reading AST as +3 gives
# a shortest lag of 15 minutes over 205 recordings and not one negative; as -4
# it puts 203 of those 205 meetings AFTER their own upload, which is
# impossible. EEST (+3, 42 recordings) and EET (+2, 30) check out the same way.
MEET_NAME_ZONES = {'AST': 3, 'EEST': 3, 'EET': 2}
MEET_NAME_ZONE = re.compile(
    r'(\d{4})[/-](\d{2})[/-](\d{2})\s+(?:at\s+)?(\d{1,2})[:_ ](\d{2})'
    r'\s*(%s)\b' % '|'.join(MEET_NAME_ZONES))

# Tokens too generic to confirm anything on their own.
NOISE = re.compile(r'\b(meet|meeting|recording|\d{4}-\d{2}-\d{2}|\(\d+\))\b', re.I)

# ---------------------------------------------------------------- notes twins
# Meet writes BOTH halves of one session with the same stem: the video as
# "<title> - 2026/08/11 14:32 GMT+03:00 - Recording" and Gemini's notes as
# "<title> - 2026/08/11 14:32 GMT+03:00 - Notes by Gemini". Measured on this
# workspace: 941 of the 953 Gemini documents in Drive carry that stamp. So a
# document does not have to be matched to a meeting by a time WINDOW at all —
# it can be matched by IDENTITY, which is what _google_notes_twin does.
NOTES_DECORATION = re.compile(
    r'[-–—]?\s*(notes by gemini|meeting notes|recording)\s*\d*\s*$', re.I)
NOTES_COPY_PREFIX = re.compile(r'^\s*(translated copy of|copy of)\s+', re.I)
# The two shapes Meet uses when the meeting had NO title of its own: the
# document becomes "Meeting started <stamp>" and the video is named after the
# meeting CODE. They are the same event, and they share not one word — so for
# these, and only these, the timestamp alone is allowed to decide.
NOTES_UNTITLED_DOC = re.compile(
    r'^\s*(?:translated copy of\s+|copy of\s+)?meeting started\b', re.I)
MEET_CODE_NAME = re.compile(
    r'^\s*(?:copy of\s+)?[a-z]{3}-[a-z]{4}-[a-z]{3}\b', re.I)
# Words that identify nobody. "شركة" (company) is on half the customer names
# here, and it alone once decided a match between two unrelated meetings that
# started in the same minute.
GENERIC_TITLE_TOKENS = {'شركة', 'مؤسسة', 'company', 'notes', 'gemini'}


class SemblyMeeting(models.Model):
    _inherit = 'sembly.meeting'

    # ------------------------------------------------------------------ fields
    source = fields.Selection(
        selection_add=[('google', "Google")], ondelete={'google': 'set default'})
    google_file_id = fields.Char(
        string="معرّف ملف التسجيل", copy=False, index=True,
        help="The Drive fileId of the Meet recording. Stable, unlike anything "
             "Sembly exposes, and what every other Google operation keys on.")
    google_recording_url = fields.Char(
        string="رابط التسجيل (Drive)", copy=False,
        help="Drive's own player link. Opening it still requires access to the "
             "file — google_share_url is the one that does not.")
    google_share_url = fields.Char(
        string="رابط المشاركة المفتوح", copy=False, tracking=True,
        help="Set once 'anyone with the link' has been granted on the "
             "recording: it opens without signing in to any account. Unlike "
             "Sembly's guest link this one can be revoked again.")
    google_owner_email = fields.Char(
        string="مالك التسجيل", copy=False,
        help="Whose Drive the recording lives in — the meeting organiser. "
             "Every Drive call about this meeting impersonates them.")
    google_notes_file_id = fields.Char(string="معرّف ملف ملاحظات Gemini", copy=False)
    gemini_notes = fields.Html(
        string="ملاحظات Gemini", sanitize=True, copy=False,
        help="Google's own meeting notes, as written by Gemini.")
    gemini_notes_ar = fields.Html(
        string="ملاحظات Gemini (بالعربية)", sanitize=True, copy=False,
        help="The same notes translated by the configured AI agent. Google's "
             "note-taker does not support Arabic, so the source document is "
             "written in the meeting's own language and translated here.")
    merged_summary_source = fields.Selection(
        [('merged', "دمج Sembly + Gemini"), ('translated', "ترجمة Gemini")],
        string="مصدر الملخص العربي", copy=False, readonly=True,
        help="Which route produced the Arabic summary: a merge of both "
             "providers, or a translation of Google's notes alone because "
             "Sembly had no summary for this meeting.")
    gemini_notes_url = fields.Char(
        string="رابط ملف ملاحظات Gemini", compute='_compute_gemini_notes_url',
        help="Opens the Google Doc itself. NOT shared publicly: whoever follows "
             "it needs their own Drive access, which is the right default for "
             "a document nobody asked to publish.")
    has_google = fields.Boolean(
        string="من Google", compute='_compute_has_google', store=True, index=True)
    provider_summary = fields.Char(
        string="المزوّدون", compute='_compute_provider_summary',
        help="Which providers have contributed to this record.")

    @api.depends('google_notes_file_id')
    def _compute_gemini_notes_url(self):
        for record in self:
            record.gemini_notes_url = (
                'https://docs.google.com/document/d/%s/edit'
                % record.google_notes_file_id) if record.google_notes_file_id else False

    @api.depends('google_file_id', 'sembly_meeting_id', 'source')
    def _compute_has_google(self):
        for record in self:
            record.has_google = bool(record.google_file_id)

    @api.depends('google_file_id', 'sembly_meeting_id', 'source')
    def _compute_provider_summary(self):
        for record in self:
            names = []
            if record.source != 'google' or record.sembly_meeting_id_is_real():
                names.append('Sembly')
            if record.google_file_id:
                names.append('Google')
            record.provider_summary = " + ".join(names) or '-'

    def sembly_meeting_id_is_real(self):
        """A Google-only record still needs the required sembly id column
        filled, so it gets a synthetic one. This tells the two apart."""
        self.ensure_one()
        return not (self.sembly_meeting_id or '').startswith('google:')

    @api.depends('summary', 'transcript', 'gemini_notes', 'gemini_notes_ar')
    def _compute_has_content(self):
        """Re-declare the trigger set so Google's own narrative counts.

        has_summary is a STORED computed field, and the base can only depend on
        its own fields. Without this override a Google-only meeting kept
        has_summary=False no matter how much Gemini wrote — so the chatter cron
        skipped it and the post button stayed hidden. The compute itself is
        unchanged; only the dependencies widen.
        """
        return super()._compute_has_content()

    @api.depends('summary', 'ai_brief', 'gemini_notes', 'gemini_notes_ar',
                 'merged_summary_source')
    def _compute_final_summary(self):
        """Widen the trigger set — the base can only depend on its own fields."""
        return super()._compute_final_summary()

    def _narrative_html(self, label):
        """The rich version of whichever source won, for display."""
        self.ensure_one()
        if label.startswith("Sembly + Gemini") or label.startswith("Gemini (مترجم)"):
            return self.gemini_notes_ar or False
        if label == "Gemini":
            return self.gemini_notes or False
        return super()._narrative_html(label)

    def _open_url(self, url, missing):
        """Open a URL in a new tab, or say plainly why there is none."""
        self.ensure_one()
        if not url:
            raise UserError(missing)
        return {'type': 'ir.actions.act_url', 'url': url, 'target': 'new'}

    def action_open_google_recording(self):
        return self._open_url(
            self.google_recording_url,
            _("No Google recording is attached to this meeting."))

    def action_open_gemini_document(self):
        return self._open_url(
            self.gemini_notes_url,
            _("No Gemini document was found for this meeting."))

    def action_open_google_share(self):
        return self._open_url(
            self.google_share_url,
            _("No public share link has been issued yet."))

    def action_show_gemini_text(self):
        self.ensure_one()
        return self._open_text_dialog(
            _("نص Gemini الأصلي"), self.gemini_notes,
            note=_("ما كتبه Gemini بلغة الاجتماع، قبل الدمج أو الترجمة."),
            link=self.gemini_notes_url)

    def _narrative_sources(self):
        """Contribute Google's account of the meeting.

        The MERGED Arabic text when it exists — it already contains both
        providers, so adding Sembly's raw summary beside it would feed the same
        material twice. Otherwise Gemini's own words.
        """
        self.ensure_one()
        from odoo.tools import html2plaintext
        if self.gemini_notes_ar and self.merged_summary_source == 'merged':
            merged = html2plaintext(self.gemini_notes_ar).strip()
            if merged:
                # Replaces both, rather than adding to them.
                return [("Sembly + Gemini (مدموج)", merged)]
        sources = super()._narrative_sources()
        arabic = html2plaintext(self.gemini_notes_ar or '').strip()
        notes = html2plaintext(self.gemini_notes or '').strip()
        if arabic:
            sources.append(("Gemini (مترجم)", arabic))
        elif notes:
            sources.append(("Gemini", notes))
        return sources

    # ------------------------------------------------------------------ client
    @api.model
    def _google_client(self, subject=None):
        import json
        raw = self._icp('sembly.google_service_account') or ''
        if not raw.strip():
            raise UserError(_(
                "No Google service account is configured. Settings → Sembly → "
                "Google."))
        try:
            info = json.loads(raw)
        except ValueError as exc:
            raise UserError(_("The Google service account JSON is not valid JSON: %s", exc))
        return GoogleWorkspaceClient(
            info, subject=subject or self._icp('sembly.google_subject') or None)

    @api.model
    def _google_enabled(self):
        return (self._icp('sembly.google_enabled', '0') in ('1', 'True', 'true')
                and bool((self._icp('sembly.google_service_account') or '').strip()))

    # ---------------------------------------------------------------- matching
    @api.model
    def _meeting_start_from_name(self, name):
        """The meeting's real start, out of the file name, in UTC.

        Returns None when the name carries no timestamp — 21% of them carry
        none at all (a renamed or hand-uploaded video), and for those the caller
        falls back to createdTime with a much wider tolerance rather than
        pretending to precision it does not have.

        Every spelling MEET_NAME_TIME accepts is handled here, including the one
        with NO offset: "GMT" alone is UTC, so the local time IS the UTC time.
        """
        match = MEET_NAME_TIME.search(name or '')
        if match:
            year, month, day, hour, minute, sign, off_h, off_m = match.groups()
            # A bare "GMT" leaves sign and offset unmatched: zero, and the
            # branch below is then a no-op either way.
            offset = timedelta(hours=int(off_h or 0), minutes=int(off_m or 0))
            if sign == '-':
                offset = -offset
        else:
            # Then the spellings that name the zone instead of offsetting it.
            match = MEET_NAME_ZONE.search(name or '')
            if not match:
                return None
            year, month, day, hour, minute, label = match.groups()
            offset = timedelta(hours=MEET_NAME_ZONES[label.upper()])
        try:
            local = datetime(int(year), int(month), int(day), int(hour), int(minute))
        except ValueError:
            return None
        return local - offset

    # ------------------------------------------------------------ notes twins
    @api.model
    def _notes_title_tokens(self, title):
        """The words of a Meet artefact's name, with the decoration removed.

        Deliberately NOT ``_google_title_tokens``: that one feeds the recording
        matcher, and the recording path must keep behaving exactly as it does.
        This one additionally strips the "- Notes by Gemini" / "- Recording"
        tail, the "Copy of" prefix, and the words that identify nobody.
        """
        text = MEET_NAME_TIME.sub(' ', title or '')
        text = MEET_NAME_ZONE.sub(' ', text)
        text = NOTES_DECORATION.sub(' ', text)
        text = NOTES_COPY_PREFIX.sub(' ', text)
        return self._google_title_tokens(text) - GENERIC_TITLE_TOKENS

    @api.model
    def _google_notes_twin(self, name):
        """The meeting a Gemini document is the other half OF.

        This is an IDENTITY, not a heuristic, and that is the whole point. The
        video and the notes are two files Meet named from the same session, so
        their stamps are the same instant to the minute — not "close", equal.
        _upsert_from_google stores exactly that instant on the record, so the
        lookup is an equality test, and the 8-hour createdTime window that
        _match_google_artifact must fall back on never comes into it.

        Why this exists at all: _match_google_artifact refuses to return a
        Google-only record, and rightly so — see the comment on its domain, a
        second RECORDING must never attach itself to the placeholder that a
        first one created. But a Gemini document is not a second recording, it
        is the same session's other half, and on a Drive-only database the
        placeholder is the ONLY record its meeting has. That exclusion is why
        all 953 documents in this workspace imported as zero.

        MEASURED over the real 953 documents and 3 155 recordings, replayed
        offline before this shipped: 783 documents land on their own record,
        170 are refused (126 whose meeting has no video in Drive at all, 30
        where two meetings share the minute and the titles do not decide, 12
        junk documents the Drive query drags in and 2 more). Then the test that
        matters — deleting each document's true twin first, i.e. pretending its
        own meeting was never recorded — leaves exactly ONE document out of 783
        attaching to a neighbour, against 25 before the title rules below.

        The rules, each earned by one of those failures:

        * the stamp must be EQUAL, to the minute;
        * when both names carry a real title, the titles must agree, and agree
          more than any rival at that minute does — "شركة" alone is not
          agreement, which is why GENERIC_TITLE_TOKENS exists;
        * when Meet wrote NO title, the stamp may decide alone — but only if
          BOTH halves say so, the document by being "Meeting started …" and
          the video by being named after the meeting code. Accepting either
          one on its own re-admits three of the misattachments.
        """
        started = self._meeting_start_from_name(name)
        if not started:
            # A document with no stamp is not a Meet artefact — on this Drive
            # they are user documents that merely contain the word "notes".
            # They match nothing, and so cost no LLM call.
            return self.browse()
        candidates = self.sudo().with_context(active_test=False).search([
            ('started_at', '=', fields.Datetime.to_string(started)),
        ])
        if not candidates:
            return self.browse()

        wanted = self._notes_title_tokens(name)
        if wanted:
            scored = sorted(
                ((len(wanted & self._notes_title_tokens(rec.name)), rec)
                 for rec in candidates),
                key=lambda pair: pair[0], reverse=True)
            if scored[0][0] and (len(scored) == 1 or scored[0][0] > scored[1][0]):
                return scored[0][1]
        if len(candidates) == 1 and NOTES_UNTITLED_DOC.match(name or '') \
                and MEET_CODE_NAME.match(candidates.name or ''):
            return candidates
        return self.browse()

    @api.model
    def _match_google_artifact(self, name, created_at, owner_email):
        """Find the Sembly meeting a Drive artefact belongs to.

        Google gives us no Sembly id, so this is a heuristic — and it is
        deliberately CONSERVATIVE, because a wrong match attaches one meeting's
        recording to another meeting's opportunity, which is worse than leaving
        it unmatched for a human to file.

        Time is the primary key: a recording starts within minutes of its
        meeting. Where several meetings sit in the same window, the title
        decides; where nothing decides, we return nothing.
        """
        # The file NAME first: it carries the meeting start, so a tight window
        # is justified. Only when the name has no timestamp do we fall back to
        # createdTime, and then the window has to be hours, not minutes,
        # because that value is an upload time.
        started = self._meeting_start_from_name(name)
        if started:
            window = self._icp_int('sembly.google_match_minutes',
                                   DEFAULT_MATCH_MINUTES)
            anchor, low, high = started, started - timedelta(minutes=window), \
                started + timedelta(minutes=window)
        elif created_at:
            # An upload never precedes its meeting (measured: 0 of 635), so the
            # window is asymmetric — generous backwards, tight forwards.
            lag = self._icp_int('sembly.google_upload_lag_minutes',
                                DEFAULT_UPLOAD_LAG_MINUTES)
            anchor, low, high = created_at, created_at - timedelta(minutes=lag), \
                created_at + timedelta(minutes=15)
        else:
            return self.browse()

        candidates = self.sudo().with_context(active_test=False).search([
            ('started_at', '>=', fields.Datetime.to_string(low)),
            ('started_at', '<=', fields.Datetime.to_string(high)),
            # NOT a Google-only record: those are placeholders this very
            # importer created, not meetings a recording can belong to. Without
            # this, a second recording in the same window attaches itself to the
            # first recording's placeholder and overwrites it.
            ('sembly_meeting_id', 'not like', 'google:%'),
        ])
        if not candidates:
            return self.browse()
        if len(candidates) == 1:
            return candidates

        # Several meetings in the window: the owner having been a participant,
        # then title overlap, then give up rather than guess.
        if owner_email:
            owned = candidates.filtered(
                lambda m: owner_email.lower() in (m.participant_emails or '').lower()
                or owner_email.lower() == (m.owner_email or '').lower())
            if len(owned) == 1:
                return owned
            candidates = owned or candidates

        wanted = self._google_title_tokens(name)
        if wanted:
            scored = [(len(wanted & self._google_title_tokens(m.name)), m)
                      for m in candidates]
            scored.sort(key=lambda pair: pair[0], reverse=True)
            if scored[0][0] and (len(scored) == 1 or scored[0][0] > scored[1][0]):
                return scored[0][1]

        # Nothing in the title decided it. With a name-derived anchor the times
        # are directly comparable, so the nearest meeting is a real answer
        # rather than a guess — but only if it is clearly nearer than the next.
        if started and len(candidates) > 1:
            by_time = sorted(
                candidates,
                key=lambda m: abs((m.started_at - anchor).total_seconds()))
            first = abs((by_time[0].started_at - anchor).total_seconds())
            second = abs((by_time[1].started_at - anchor).total_seconds())
            if second - first >= 300:      # five clear minutes apart
                return by_time[0]
        return self.browse()

    @api.model
    def _google_title_tokens(self, title):
        cleaned = NOISE.sub(' ', title or '')
        return {t.lower() for t in re.split(r'[^\w؀-ۿ]+', cleaned)
                if len(t) > 2}

    # ------------------------------------------------------------------ import
    @api.model
    def _upsert_from_google(self, recording, owner_email):
        """Attach a Drive recording to its meeting, creating one if needed.

        A recording with no Sembly counterpart still becomes a meeting — that
        is what makes Google usable as a provider ON ITS OWN, which is one of
        the four supported modes.
        """
        file_id = recording.get('id')
        if not file_id:
            return self.browse()

        existing = self.sudo().with_context(active_test=False).search(
            [('google_file_id', '=', file_id)], limit=1)
        created_at = self._parse_dt(recording.get('createdTime'))
        values = {
            'google_file_id': file_id,
            'google_recording_url': recording.get('webViewLink') or False,
            'google_owner_email': owner_email or False,
        }
        if existing:
            existing.sudo().with_context(sembly_sync=True).write(values)
            return existing

        meeting = self._match_google_artifact(
            recording.get('name'), created_at, owner_email)
        if meeting and meeting.google_file_id \
                and meeting.google_file_id != file_id:
            # A second recording for a meeting that already has one — somebody
            # recorded twice. Overwriting would silently lose whichever came
            # first, including a share link already issued on it, so this one
            # gets its own record and the collision is reported.
            self.env['sembly.sync.log']._log(
                'google', 'sync', 'error',
                "meeting %s already holds recording %s, so %s was kept as a "
                "separate record — a person should decide which is wanted."
                % (meeting.sembly_meeting_id, meeting.google_file_id, file_id))
            meeting = self.browse()
        if meeting:
            meeting.sudo().with_context(sembly_sync=True).write(values)
            return meeting

        # Google-only meeting.
        duration = 0
        meta = recording.get('videoMediaMetadata') or {}
        if meta.get('durationMillis'):
            try:
                duration = int(int(meta['durationMillis']) / 1000)
            except (TypeError, ValueError):
                duration = 0
        values.update({
            'sembly_meeting_id': 'google:%s' % file_id,
            'name': recording.get('name') or _("Google Meet recording"),
            # The name's timestamp, not createdTime: a Google-only record must
            # carry the time the meeting HAPPENED, or it lands hours late in
            # the calendar and outside the chatter window.
            'started_at': (self._meeting_start_from_name(recording.get('name'))
                           or created_at or fields.Datetime.now()),
            'duration_seconds': duration,
            'platform': 'GOOGLE_MEET',
            'owner_email': owner_email or False,
            'source': 'google',
        })
        return self.sudo().with_context(sembly_sync=True).create(values)

    def _apply_gemini_notes(self, text, file_id=None):
        """Store Google's notes, then produce ONE Arabic summary.

        Three routes, and which one runs is decided by what we actually hold:

        * BOTH summaries -> merge them in a single call. The two providers
          watched the same meeting from different angles: Sembly extracts
          decisions, tasks and risks; Gemini writes prose over Google's own
          transcript. Merging beats translating because each covers what the
          other missed, and one call costs the same as one translation.
        * GOOGLE ONLY -> translate. There is nothing to merge with.
        * SEMBLY ONLY -> nothing to do here at all; the base app already owns
          that summary.

        Google writes these notes in ENGLISH even for an Arabic meeting —
        measured, not assumed: two Arabic-titled documents came back 2% Arabic
        letters, the rest English prose. So an Arabic output is required either
        way.

        Failure is never fatal. The imported notes are worth having on their
        own, so a model error leaves them in place and simply skips the Arabic.
        """
        self.ensure_one()
        # Test the TEXT, not the markup built from it: plaintext2html('')
        # returns '<p></p>', which survives html_sanitize and is truthy — so
        # this guard never fired. An empty export was stored as blank notes,
        # marked imported for good, and paid for a translation of nothing.
        if not (text or '').strip():
            return False
        body = html_sanitize(plaintext2html(text))
        if not body:
            return False
        values = {'gemini_notes': body}
        if file_id:
            values['google_notes_file_id'] = file_id
        self.sudo().with_context(sembly_sync=True).write(values)

        if self._icp('sembly.google_translate_notes', '1') not in ('1', 'True', 'true'):
            return True

        from odoo.tools import html2plaintext
        sembly_text = html2plaintext(self.summary or '').strip()
        google_text = (text or '').strip()

        if sembly_text:
            prompt = (
                "لديك ملخّصان لاجتماع واحد من مصدرين مختلفين. ادمجهما في ملخّص "
                "عربي واحد متكامل، بلا تكرار وبلا تناقض.\n"
                "المصدر الأول (Sembly) يستخرج القرارات والمهام والمخاطر، "
                "والثاني (Gemini من Google) يكتب سرداً على تفريغ Google — فكل "
                "منهما يغطي ما فوّته الآخر، فاحتفظ بكل معلومة فريدة.\n"
                "عند التعارض في رقم أو اسم أو تاريخ، أورد ما تتفق عليه المصادر "
                "ونبّه على الاختلاف بإيجاز بدل اختيار أحدهما.\n"
                "أعد HTML بسيطاً فقط (<h4> و<ul><li>) بلا أي مقدمة أو تعليق.\n\n"
                "=== ملخص Sembly ===\n%s\n\n=== ملاحظات Gemini ===\n%s"
            ) % (sembly_text[:8000], google_text[:8000])
            operation = 'gemini-merge'
        else:
            prompt = (
                "ترجم ملاحظات الاجتماع التالية إلى العربية ترجمة دقيقة، مع "
                "الحفاظ على البنية والعناوين والنقاط كما هي، وأعد HTML بسيطاً "
                "فقط بلا أي مقدمة أو تعليق:\n\n%s" % google_text[:12000])
            operation = 'gemini-translate'

        try:
            answer = self._ask_agent(prompt)
        except Exception as exc:  # noqa: BLE001 - the notes still stand
            self.env['sembly.sync.log']._log(
                'ai', operation, 'error',
                "meeting %s: %s" % (self.sembly_meeting_id, exc))
            return True

        arabic = self._coerce_html(answer)
        if arabic:
            self.sudo().with_context(sembly_sync=True).write({
                'gemini_notes_ar': arabic,
                'merged_summary_source': 'merged' if sembly_text else 'translated',
            })
        return True

    # ------------------------------------------------------------------ crons
    @api.model
    def _google_subjects(self):
        """Whose Drive to sweep. ONE account by default, not everybody.

        Impersonating every employee was the original design and it is not
        needed: ``files.list`` returns everything the impersonated user can
        SEE, which includes files shared with them, not merely files they own.
        Measured on this workspace — 1 108 recordings are visible to
        crm@era.net.sa while only 77 are owned by it; 922 belong to another
        colleague and are visible because they are shared. One narrow
        impersonation therefore finds the same history as sixteen, with a
        sixteenth of the exposure.

        ``sembly.google_subjects`` still accepts an explicit list for the case
        where a second account holds recordings nobody shared.
        """
        explicit = self._icp('sembly.google_subjects') or ''
        if explicit.strip():
            return [s.strip() for s in re.split(r'[,;\s]+', explicit) if s.strip()]
        subject = (self._icp('sembly.google_subject') or '').strip()
        return [subject] if subject else []

    @api.model
    def _cron_sync_google(self):
        """Sweep every internal user's Drive for recordings and Gemini notes."""
        if not self._google_enabled():
            return True
        Log = self.env['sembly.sync.log']
        base = self._google_client()
        days = self._icp_int('sembly.google_window_days', 7)
        after = fields.Datetime.to_string(
            fields.Datetime.now() - timedelta(days=days)).replace(' ', 'T') + 'Z'

        recordings = notes = matched = 0
        for subject in self._google_subjects():
            client = base.for_user(subject)
            try:
                found = client.list_recordings(after=after)
            except GoogleWorkspaceError as exc:
                # One employee's Drive being unreachable must not stop the rest.
                Log._log('google', 'sync', 'error', "%s: %s" % (subject, exc))
                continue
            for item in found:
                meeting = self._upsert_from_google(item, subject)
                recordings += 1
                if meeting and meeting.sembly_meeting_id_is_real():
                    matched += 1
                if self._may_commit():
                    self.env.cr.commit()

            try:
                docs = client.list_gemini_notes(after=after)
            except GoogleWorkspaceError as exc:
                Log._log('google', 'notes', 'error', "%s: %s" % (subject, exc))
                continue
            for doc in docs:
                # The twin FIRST: it is an identity, so when it answers it is
                # right, and it is the only route that can reach a Google-only
                # record at all. The window heuristic stays as the fallback for
                # a document Meet did not stamp.
                meeting = self._google_notes_twin(doc.get('name')) or \
                    self._match_google_artifact(
                        doc.get('name'), self._parse_dt(doc.get('createdTime')),
                        subject)
                if not meeting or meeting.gemini_notes:
                    continue
                try:
                    meeting._apply_gemini_notes(
                        client.export_document_text(doc['id']), doc['id'])
                    notes += 1
                except GoogleWorkspaceError as exc:
                    Log._log('google', 'notes', 'error', "%s: %s" % (doc.get('id'), exc))
                if self._may_commit():
                    self.env.cr.commit()

        Log._log('google', 'sync', 'ok',
                 "%s recording(s), %s matched to a Sembly meeting, %s Gemini "
                 "note(s). Arabic meetings produce no notes by design."
                 % (recordings, matched, notes), meeting_count=recordings)
        return True

    @api.model
    def _upsert_from_mcp(self, meta, details=None):
        """After Sembly's own upsert, look for a recording that arrived first."""
        record = super()._upsert_from_mcp(meta, details)
        if record:
            self._adopt_orphan_google_record(record)
        return record

    @api.model
    def _upsert_from_webhook(self, payload, kind):
        record = super()._upsert_from_webhook(payload, kind)
        if record:
            self._adopt_orphan_google_record(record)
        return record

    # ------------------------------------------------------- the other direction
    # Matching used to run ONE WAY: only when a Google recording arrived. But
    # Sembly holds a meeting back while it processes it, so the recording is
    # routinely in Drive first and a Google-only record gets created. When
    # Sembly finally delivers the same meeting, nothing looked back — leaving
    # two records for one meeting, and the recording stranded on the one with
    # no summary, no items and no link fields.
    @api.model
    def _adopt_orphan_google_record(self, meeting):
        """Fold a Google-only record into the Sembly meeting that just arrived.

        Moves the Google artefacts ONTO the Sembly record and deletes the
        orphan, rather than the reverse: the Sembly record is the one the
        matcher, the chatter and every link field already know about, and it may
        already carry a human's hand-made link.

        Conservative on purpose — it only adopts when exactly ONE orphan sits in
        the window, because merging the wrong recording into a meeting is worse
        than leaving a duplicate for a person to see.
        """
        if not meeting or not meeting.started_at:
            return False
        if meeting.google_file_id or not meeting.sembly_meeting_id_is_real():
            return False

        window = self._icp_int('sembly.google_match_minutes', DEFAULT_MATCH_MINUTES)
        low = meeting.started_at - timedelta(minutes=window)
        high = meeting.started_at + timedelta(minutes=window)
        orphans = self.sudo().with_context(active_test=False).search([
            ('sembly_meeting_id', '=like', 'google:%'),
            ('google_file_id', '!=', False),
            ('started_at', '>=', fields.Datetime.to_string(low)),
            ('started_at', '<=', fields.Datetime.to_string(high)),
        ])
        if len(orphans) != 1:
            if len(orphans) > 1:
                self.env['sembly.sync.log']._log(
                    'google', 'adopt', 'error',
                    "meeting %s: %s orphan Google records in the window, so none "
                    "was adopted — a person should look."
                    % (meeting.sembly_meeting_id, len(orphans)))
            return False

        orphan = orphans
        values = {
            'google_file_id': orphan.google_file_id,
            'google_recording_url': orphan.google_recording_url,
            'google_owner_email': orphan.google_owner_email,
        }
        # Carry the notes across only if Sembly's arrival has none of its own.
        # The ARABIC comes with them: it cost an LLM call, the orphan is about
        # to be unlinked, and the regeneration below only fires when Sembly
        # brought a summary to merge with — so leaving gemini_notes_ar behind
        # threw the translation away for good whenever it did not. Harmless
        # until now only because a placeholder could never hold notes in the
        # first place; _google_notes_twin is exactly what changes that.
        if orphan.gemini_notes and not meeting.gemini_notes:
            values.update({
                'gemini_notes': orphan.gemini_notes,
                'gemini_notes_ar': orphan.gemini_notes_ar,
                'merged_summary_source': orphan.merged_summary_source,
                'google_notes_file_id': orphan.google_notes_file_id,
            })
        if orphan.google_share_url:
            values['google_share_url'] = orphan.google_share_url
            values.setdefault('share_url', orphan.google_share_url)
        meeting.sudo().with_context(sembly_sync=True).write(values)

        self.env['sembly.sync.log']._log(
            'google', 'adopt', 'ok',
            "Google record %s folded into Sembly meeting %s (recording arrived "
            "first while Sembly was still processing)."
            % (orphan.sembly_meeting_id, meeting.sembly_meeting_id))
        orphan.sudo().unlink()

        # The notes just moved in and there was no Sembly summary at the time
        # they were written, so the Arabic summary is a translation of Google
        # alone. Now that Sembly's summary exists, the merge is the better
        # answer — redo it.
        if meeting.gemini_notes and meeting.summary and \
                meeting.merged_summary_source != 'merged':
            from odoo.tools import html2plaintext
            meeting._apply_gemini_notes(html2plaintext(meeting.gemini_notes))
        return True

    # --------------------------------------------------------------- backfill
    # Drive is the ONLY route to history: Google deletes conferenceRecords after
    # 30 days, while the MP4s in Drive have no expiry — measured on this
    # workspace, 1 108 recordings reaching back to 2024-01-08, seven months
    # FURTHER back than Sembly's own oldest meeting. Same shape as the Sembly
    # backfill: armed by a human, resumable, time-boxed, self-terminating.
    @api.model
    def _start_google_backfill(self):
        icp = self.env['ir.config_parameter'].sudo()
        icp.set_param('sembly.google_backfill_state', 'running')
        icp.set_param('sembly.google_backfill_token', '')
        icp.set_param('sembly.google_backfill_seen', '0')
        icp.set_param('sembly.google_backfill_matched', '0')
        cron = self.env.ref('era_sembly_meetings_google.cron_google_backfill',
                            raise_if_not_found=False)
        if cron:
            cron.sudo().write({'active': True, 'nextcall': fields.Datetime.now()})
        return True

    @api.model
    def _cron_google_backfill(self):
        """Walk the whole of Drive once, oldest history included.

        Pages on Drive's own ``nextPageToken`` rather than on dates, because a
        page token is exactly a resumable cursor. Never writes its own ir_cron
        row — a cron worker holds a lock on that row in a separate transaction,
        so a job that disarms itself deadlocks (learned the hard way).
        """
        icp = self.env['ir.config_parameter'].sudo()
        if (icp.get_param('sembly.google_backfill_state') or 'idle') != 'running':
            return True
        if not self._google_enabled():
            icp.set_param('sembly.google_backfill_state', 'error')
            return True

        Log = self.env['sembly.sync.log']
        subjects = self._google_subjects()
        if not subjects:
            icp.set_param('sembly.google_backfill_state', 'error')
            Log._log('google', 'backfill', 'error',
                     "No account configured to sweep.")
            return True

        client = self._google_client(subject=subjects[0])
        budget = self._icp_int('sembly.google_backfill_seconds', 90)
        deadline = time.monotonic() + budget
        token = icp.get_param('sembly.google_backfill_token') or None
        seen = self._icp_int('sembly.google_backfill_seen', 0)
        matched = self._icp_int('sembly.google_backfill_matched', 0)

        while True:
            params = {
                'q': "mimeType='video/mp4' and trashed=false",
                'fields': 'nextPageToken, files(id, name, createdTime, size, '
                          'webViewLink, videoMediaMetadata, owners(emailAddress))',
                'pageSize': 100,
                'spaces': 'drive',
                'supportsAllDrives': 'true',
                'includeItemsFromAllDrives': 'true',
                'orderBy': 'createdTime desc',
            }
            if token:
                params['pageToken'] = token
            try:
                data = client._call(
                    'GET', 'https://www.googleapis.com/drive/v3/files',
                    params=params) or {}
            except GoogleWorkspaceError as exc:
                # Leave the token untouched so the next tick retries this page
                # rather than skipping past it.
                Log._log('google', 'backfill', 'error', str(exc))
                return True

            for item in data.get('files') or []:
                owner = ((item.get('owners') or [{}])[0]).get('emailAddress')
                meeting = self._upsert_from_google(item, owner or subjects[0])
                seen += 1
                if meeting and meeting.sembly_meeting_id_is_real():
                    matched += 1
                if self._may_commit():
                    self.env.cr.commit()

            token = data.get('nextPageToken')
            icp.set_param('sembly.google_backfill_token', token or '')
            icp.set_param('sembly.google_backfill_seen', str(seen))
            icp.set_param('sembly.google_backfill_matched', str(matched))
            if self._may_commit():
                self.env.cr.commit()

            if not token:
                icp.set_param('sembly.google_backfill_state', 'done')
                Log._log('google', 'backfill', 'ok',
                         "Drive sweep complete: %s recording(s), %s matched to "
                         "an existing meeting." % (seen, matched),
                         meeting_count=seen)
                return True
            if time.monotonic() >= deadline:
                Log._log('google', 'backfill', 'ok',
                         "%s recording(s) so far, %s matched; resuming at the "
                         "next page." % (seen, matched), meeting_count=seen)
                return True

    @api.model
    def _cron_google_notes_backfill(self):
        """Import and summarise the Gemini documents, separately.

        Deliberately its own cron: the notes cost an LLM call each — 398 of them
        on this workspace — while the recordings cost nothing. Splitting them
        means the recordings and their share links land immediately and for
        free, and the summarising can be paced, paused or postponed without
        holding that up.
        """
        icp = self.env['ir.config_parameter'].sudo()
        if (icp.get_param('sembly.google_notes_state') or 'idle') != 'running':
            return True
        if not self._google_enabled():
            return True

        Log = self.env['sembly.sync.log']
        subjects = self._google_subjects()
        if not subjects:
            return True
        client = self._google_client(subject=subjects[0])
        budget = self._icp_int('sembly.google_backfill_seconds', 90)
        deadline = time.monotonic() + budget

        # page_limit is the ONLY thing standing between this sweep and a silent
        # gap. _list_files stops after that many pages of 100 and throws the
        # remaining pageToken away, so documents past the ceiling are not
        # "later" — they are unreachable, by this tick and by every tick after
        # it, because the listing restarts from the newest each time. This
        # workspace holds 953 of them: at the old ceiling of 10 pages it was 47
        # documents away from losing the tail without a word in the log.
        # Raised to a size no Drive of meeting notes reaches soon, and the
        # count is reported below so the day it IS reached is visible.
        pages = max(10, self._icp_int('sembly.google_notes_pages', 60))
        try:
            docs = client.list_gemini_notes(page_limit=pages)
        except GoogleWorkspaceError as exc:
            Log._log('google', 'notes-backfill', 'error', str(exc))
            return True
        if len(docs) >= pages * 100:
            Log._log('google', 'notes-backfill', 'error',
                     "The listing came back full at %s documents, so Drive may "
                     "hold more that this sweep cannot see. Raise "
                     "sembly.google_notes_pages." % len(docs))

        # Which documents are already in, asked ONCE. This used to be a
        # search_count per document — 1 ms each on an unindexed column, so
        # nothing at 40 documents and about a second at 800, every tick,
        # forever. One query costs the same as one of them.
        imported_ids = set(self.sudo().with_context(active_test=False).search(
            [('google_notes_file_id', '!=', False)]).mapped('google_notes_file_id'))

        done = already = unmatched = failed = 0
        exhausted = True
        for doc in docs:
            if time.monotonic() >= deadline:
                # Out of budget, NOT out of work — and the difference decides
                # whether this run is allowed to call itself finished below.
                exhausted = False
                break
            if doc['id'] in imported_ids:
                already += 1
                continue
            # See _cron_sync_google: identity first, heuristic as the fallback.
            meeting = self._google_notes_twin(doc.get('name')) or \
                self._match_google_artifact(
                    doc.get('name'), self._parse_dt(doc.get('createdTime')),
                    ((doc.get('owners') or [{}])[0]).get('emailAddress'))
            if not meeting:
                unmatched += 1
                continue
            if meeting.gemini_notes:
                # The live sync has always guarded this; the backfill did not,
                # so a second document for one meeting overwrote the first's
                # notes and spent a second LLM call doing it.
                already += 1
                continue
            try:
                meeting._apply_gemini_notes(
                    client.export_document_text(doc['id']), doc['id'])
                imported_ids.add(doc['id'])
                done += 1
            except GoogleWorkspaceError as exc:
                failed += 1
                Log._log('google', 'notes-backfill', 'error',
                         "%s: %s" % (doc.get('id'), exc))
            if self._may_commit():
                self.env.cr.commit()

        # "Nothing imported" is NOT "nothing left to do". Treating the two as
        # the same is what ended this backfill after a single tick in which all
        # 953 documents failed to match, and wrote it to the log as a success —
        # the run then latched off and nothing revisited it. Finish only when
        # the listing was walked to its end AND nothing errored on the way; a
        # Google outage must leave the run armed for the next tick.
        summary = ("%s imported, %s already had notes, %s matched no meeting, "
                   "%s failed, out of %s document(s) listed."
                   % (done, already, unmatched, failed, len(docs)))
        if exhausted and not done and not failed:
            icp.set_param('sembly.google_notes_state', 'done')
            Log._log('google', 'notes-backfill', 'ok',
                     "Finished — nothing left to import. " + summary)
        else:
            Log._log('google', 'notes-backfill', 'ok', summary, meeting_count=done)
        return True

    @api.model
    def _start_google_notes_backfill(self):
        self.env['ir.config_parameter'].sudo().set_param(
            'sembly.google_notes_state', 'running')
        cron = self.env.ref('era_sembly_meetings_google.cron_google_notes_backfill',
                            raise_if_not_found=False)
        if cron:
            cron.sudo().write({'active': True, 'nextcall': fields.Datetime.now()})
        return True

    # ----------------------------------------------------------------- actions
    def _check_share_access(self):
        """A button's groups= attribute hides it; it does not stop an RPC call.

        Publishing a recording to anyone-with-the-link is the most consequential
        thing this module can do, so it is checked SERVER-SIDE (rule 19) as well
        as hidden in the view.
        """
        if self.env.su:
            return
        if not self.env.user.has_group('era_sembly_meetings.group_sembly_share'):
            raise AccessError(_(
                "Publishing a meeting recording needs the 'Share meeting "
                "recordings publicly' right. Sales, project and helpdesk "
                "managers hold it."))

    def action_google_share_link(self):
        """Issue the open link — the thing Sembly cannot do at all."""
        self.ensure_one()
        self._check_share_access()
        if not self.google_file_id:
            raise UserError(_("This meeting has no Google recording attached."))
        client = self._google_client(subject=self.google_owner_email or None)
        try:
            url = client.share_anyone_with_link(self.google_file_id)
        except GoogleWorkspaceError as exc:
            raise UserError(_("Google refused to share the recording: %s", exc)) from exc
        self.sudo().with_context(sembly_sync=True).write({
            'google_share_url': url,
            'share_url': self.share_url or url,
        })
        return {
            'type': 'ir.actions.client', 'tag': 'display_notification',
            'params': {'title': _("Google"), 'type': 'success',
                       'message': _("A link anyone can open has been created.")},
        }

    def action_google_revoke_link(self):
        """Revoke it again — Sembly's guest links can never be withdrawn."""
        self.ensure_one()
        self._check_share_access()
        if not self.google_file_id:
            return False
        client = self._google_client(subject=self.google_owner_email or None)
        try:
            removed = client.revoke_link_sharing(self.google_file_id)
        except GoogleWorkspaceError as exc:
            raise UserError(_("Google refused to revoke the link: %s", exc)) from exc
        self.sudo().with_context(sembly_sync=True).write({'google_share_url': False})
        return {
            'type': 'ir.actions.client', 'tag': 'display_notification',
            'params': {'title': _("Google"), 'type': 'success',
                       'message': _("%s public permission(s) removed.", removed)},
        }

    # -------------------------------------------------------------------- seam
    def _sembly_content_fields(self):
        return super()._sembly_content_fields() | {
            'google_file_id', 'google_recording_url', 'google_owner_email',
            'google_notes_file_id', 'gemini_notes', 'gemini_notes_ar',
            'merged_summary_source',
        }

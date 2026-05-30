"""ERA SEO — Redirect manager.

Stores 301/302/307/308/410 redirects and resolves them on every request.
The runtime hook lives in :pyclass:`ir.http` (see ``models/ir_http.py``);
this file only holds the data model and matching logic.

Per SPEC §9.
"""
import logging
import re
from urllib.parse import urlparse

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)

# Redirect types eligible for a (source, target) round trip.
_TYPES_WITH_TARGET = ('301', '302', '307', '308')

# Hard cap on per-request redirect hops before we declare a loop.
# Five matches the figure in SPEC §9.2.
REDIRECT_HOP_LIMIT = 5


class EraSeoRedirect(models.Model):
    """A single redirect rule, scoped optionally to a website and/or language."""

    _name = 'era.seo.redirect'
    _description = 'SEO Redirect'
    _order = 'sequence, id'
    _rec_name = 'name'

    name = fields.Char(string='Name', compute='_compute_name', store=True)
    sequence = fields.Integer(default=10, index=True)

    source_url = fields.Char(
        string='Source Path',
        required=True,
        index=True,
        help='The incoming request path to match. Always starts with "/". '
             'When "Is Regex" is set, this is a Python regex pattern matched '
             'with re.fullmatch against the request path.',
    )
    target_url = fields.Char(
        string='Target',
        help='The destination path or full URL. May contain backreferences '
             '(\\1, \\2, ...) when "Is Regex" is set. Required for all '
             'redirect types except 410 (Gone).',
    )
    redirect_type = fields.Selection(
        [
            ('301', '301 — Moved Permanently'),
            ('302', '302 — Found (Temporary)'),
            ('307', '307 — Temporary (Strict Method)'),
            ('308', '308 — Permanent (Strict Method)'),
            ('410', '410 — Gone (No Target)'),
        ],
        default='301',
        required=True,
        index=True,
    )
    is_regex = fields.Boolean(
        string='Is Regex',
        default=False,
        help='Treat Source Path as a Python regex. Backreferences in Target '
             '(e.g. \\1) are substituted from regex capture groups.',
    )
    is_active = fields.Boolean(default=True, index=True)
    website_id = fields.Many2one(
        'website',
        string='Website',
        ondelete='cascade',
        index=True,
        help='Restrict this redirect to one website. Empty = all websites.',
    )
    lang_id = fields.Many2one(
        'res.lang',
        string='Language',
        ondelete='cascade',
        index=True,
        help='Restrict this redirect to one language. Empty = all languages.',
    )
    hit_count = fields.Integer(default=0, readonly=True)
    last_hit_date = fields.Datetime(readonly=True)
    notes = fields.Text()
    created_from = fields.Selection(
        [
            ('manual', 'Manual'),
            ('import', 'CSV Import'),
            ('auto_404', 'Auto from 404 Log'),
            ('auto_rename', 'Auto from URL Rename'),
        ],
        default='manual',
        readonly=True,
    )

    # --- SQL constraints / indexes -------------------------------------------

    def init(self):
        """Composite index supporting the per-request lookup query."""
        self.env.cr.execute("""
            CREATE INDEX IF NOT EXISTS era_seo_redirect_lookup_idx
            ON era_seo_redirect (is_active, source_url, website_id, lang_id)
        """)

    # --- Constraints ---------------------------------------------------------

    @api.constrains('source_url')
    def _check_source_url(self):
        for rec in self:
            src = (rec.source_url or '').strip()
            if not src:
                raise ValidationError(_('Source Path is required.'))
            if not rec.is_regex and not src.startswith('/'):
                raise ValidationError(
                    _('Source Path must start with "/": %s', src)
                )
            if rec.is_regex:
                try:
                    re.compile(src)
                except re.error as exc:
                    raise ValidationError(
                        _('Invalid regex in Source Path: %s', exc)
                    ) from exc

    @api.constrains('target_url', 'redirect_type')
    def _check_target_url(self):
        for rec in self:
            if rec.redirect_type == '410':
                continue
            if not (rec.target_url or '').strip():
                raise ValidationError(
                    _('Target is required for redirect type %s.',
                      rec.redirect_type)
                )

    @api.constrains('source_url', 'target_url', 'is_regex')
    def _check_no_self_loop(self):
        """Reject obvious source == target self-loops on plain (non-regex) rules."""
        for rec in self:
            if rec.is_regex or not rec.target_url:
                continue
            if (rec.source_url or '').strip() == (rec.target_url or '').strip():
                raise ValidationError(
                    _('Source and Target must differ on a non-regex redirect.')
                )

    # --- Computes ------------------------------------------------------------

    @api.depends('source_url', 'target_url', 'redirect_type')
    def _compute_name(self):
        for rec in self:
            arrow = ' → ' if rec.redirect_type != '410' else ' ✕ '
            target = rec.target_url or _('(gone)')
            rec.name = '[{code}] {src}{arrow}{tgt}'.format(
                code=rec.redirect_type or '?',
                src=rec.source_url or '?',
                arrow=arrow,
                tgt=target,
            )

    # --- Public matching API -------------------------------------------------

    @api.model
    def _find_match(self, path, website=None, lang=None):
        """Return the first active redirect that matches ``path`` for the scope.

        Lookup order:
          1. Plain (non-regex) rules matched exactly. Most specific scope wins:
             website+lang > website-only > lang-only > unscoped. Within a tier,
             ``sequence`` decides.
          2. Regex rules, ordered by sequence. First ``re.fullmatch`` wins.

        :param path:    Request path including a leading slash (no query string).
        :param website: ``website`` recordset or ``None``.
        :param lang:    ``res.lang`` recordset or ``None``.
        :returns:       A tuple ``(redirect, computed_target)`` on match, where
                        ``computed_target`` is the regex-substituted target
                        when applicable; or ``(None, None)`` on miss.
        """
        if not path:
            return None, None

        Redirect = self.sudo()
        website_id = website.id if website else False
        lang_id = lang.id if lang else False

        # --- Plain match (exact path equality) -------------------------------
        plain = Redirect.search([
            ('is_active', '=', True),
            ('is_regex', '=', False),
            ('source_url', '=', path),
            ('website_id', 'in', [website_id, False] if website_id else [False]),
            ('lang_id', 'in', [lang_id, False] if lang_id else [False]),
        ])

        if plain:
            # Score: 2 = matches both website and lang, 1 = matches one, 0 = unscoped.
            def _scope_score(rec):
                score = 0
                if website_id and rec.website_id.id == website_id:
                    score += 1
                if lang_id and rec.lang_id.id == lang_id:
                    score += 1
                return score
            best = sorted(plain, key=lambda r: (-_scope_score(r), r.sequence, r.id))[0]
            if best.redirect_type == '410':
                return best, None
            return best, best.target_url

        # --- Regex match -----------------------------------------------------
        regex_rules = Redirect.search([
            ('is_active', '=', True),
            ('is_regex', '=', True),
            ('website_id', 'in', [website_id, False] if website_id else [False]),
            ('lang_id', 'in', [lang_id, False] if lang_id else [False]),
        ])
        for rule in regex_rules:
            try:
                m = re.fullmatch(rule.source_url, path)
            except re.error:
                _logger.warning(
                    'era.seo.redirect[%d]: invalid regex %r, skipping',
                    rule.id, rule.source_url,
                )
                continue
            if m is None:
                continue
            if rule.redirect_type == '410':
                return rule, None
            try:
                target = m.expand(rule.target_url or '')
            except re.error as exc:
                _logger.warning(
                    'era.seo.redirect[%d]: bad backreference in target %r: %s',
                    rule.id, rule.target_url, exc,
                )
                continue
            return rule, target

        return None, None

    def _register_hit(self):
        """Increment hit_count and stamp last_hit_date.

        Uses an atomic in-DB ``hit_count = hit_count + 1`` instead of a
        read-modify-write, so two concurrent hits on the same hot rule can't
        lose an increment. Still wrapped in a savepoint: under REPEATABLE READ
        two concurrent UPDATEs of the same row can still raise 40001, and a
        redirect response must never fail over a counter. Called from the
        ir.http hook on every match.
        """
        if not self:
            return
        try:
            self.ensure_one()
            with self.env.cr.savepoint():
                self.env.cr.execute(
                    "UPDATE era_seo_redirect "
                    "SET hit_count = COALESCE(hit_count, 0) + 1, "
                    "    last_hit_date = (now() AT TIME ZONE 'UTC') "
                    "WHERE id = %s",
                    (self.id,),
                )
                self.invalidate_recordset(['hit_count', 'last_hit_date'])
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                'era.seo.redirect[%d]: hit counter update failed: %s',
                self.id, exc,
            )

    # --- Normalization helpers ----------------------------------------------

    @api.model
    def _normalize_path(self, path):
        """Strip query/fragment and ensure a leading slash. Used by the hook."""
        if not path:
            return '/'
        # If a full URL slips in, take the path portion only.
        if '://' in path:
            path = urlparse(path).path or '/'
        path = path.split('?', 1)[0].split('#', 1)[0]
        if not path.startswith('/'):
            path = '/' + path
        return path

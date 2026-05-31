"""ERA SEO — audit runner.

One ``era.seo.audit.run`` row per scheduled or manual scan. Each run
iterates the registered checks (the ``_check_*`` methods on this model)
and records findings in ``era.seo.audit.finding``.

Per SPEC §13.
"""
import logging
import re
import threading
from collections import Counter, defaultdict

from lxml import html as lxml_html

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)

_TRUE = ('True', '1', 'true', 'yes', 'on')

# Thread-local storage for the current run's seen-finding key set.
# Odoo ORM recordsets reject arbitrary attribute assignment, so we carry
# this mutable state here instead of on ``self``.
_run_local = threading.local()

# Title/description length thresholds (chars). Per SPEC §13.2.
_TITLE_TOO_LONG = 60
_TITLE_TOO_SHORT = 20
_DESC_TOO_LONG = 160
_DESC_TOO_SHORT = 70

# Slug rules.
_SLUG_TOO_LONG = 75
_SLUG_STOPWORDS = {
    'en': {'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to',
           'for', 'of', 'with', 'by'},
    'ar': {'في', 'من', 'إلى', 'على', 'عن', 'مع'},
}

# Content thresholds.
_THIN_CONTENT_WORDS = 300

# Timeout (seconds) for fetching a page's rendered public HTML during the
# DOM checks (H1, image alt, thin content). Kept short so an unreachable or
# slow page degrades to stored-arch parsing rather than stalling the audit.
_RENDER_FETCH_TIMEOUT_S = 8

# Redirect chain depth.
_REDIRECT_CHAIN_MAX = 3


class EraSeoAuditRun(models.Model):
    _name = 'era.seo.audit.run'
    _description = 'SEO Audit Run'
    _order = 'date_started desc, id desc'
    _rec_name = 'name'

    name = fields.Char(string='Name', compute='_compute_name', store=True)
    state = fields.Selection(
        [
            ('draft', 'Draft'),
            ('running', 'Running'),
            ('done', 'Done'),
            ('failed', 'Failed'),
        ],
        default='draft',
        required=True,
        readonly=True,
    )
    date_started = fields.Datetime(string='Started', readonly=True)
    date_finished = fields.Datetime(string='Finished', readonly=True)
    website_id = fields.Many2one(
        'website',
        string='Website',
        ondelete='set null',
        help='Optional. When set, the audit scans only this website. '
             'Empty = every website.',
    )
    pages_scanned = fields.Integer(readonly=True)
    error_message = fields.Text(readonly=True)

    # True when this specific run is the one currently queued / executing
    # in the background cron. Drives the form spinner and hides the
    # Run/Re-run button while the audit is in flight. Reads from ICP so
    # the OWL poll widget sees a fresh value on every tick.
    is_pending = fields.Boolean(
        compute='_compute_is_pending', string='Audit pending')

    def _compute_is_pending(self):
        ICP = self.env['ir.config_parameter'].sudo()
        active = ICP.get_param('era_seo.audit_pending') in _TRUE
        try:
            pending_run_id = int(
                ICP.get_param('era_seo.audit_pending_run_id', '0') or 0)
        except (TypeError, ValueError):
            pending_run_id = 0
        for rec in self:
            rec.is_pending = active and rec.id == pending_run_id

    finding_ids = fields.One2many(
        'era.seo.audit.finding',
        'run_id',
        string='Findings',
    )

    critical_count = fields.Integer(
        compute='_compute_counts', store=True,
    )
    warning_count = fields.Integer(
        compute='_compute_counts', store=True,
    )
    info_count = fields.Integer(
        compute='_compute_counts', store=True,
    )
    total_count = fields.Integer(
        compute='_compute_counts', store=True,
    )
    unresolved_count = fields.Integer(
        compute='_compute_counts', store=True,
    )

    # --- Computes ---------------------------------------------------------

    @api.depends('date_started', 'state')
    def _compute_name(self):
        for rec in self:
            stamp = (rec.date_started or fields.Datetime.now()).strftime('%Y-%m-%d %H:%M')
            rec.name = 'Audit {}'.format(stamp)

    @api.depends('finding_ids', 'finding_ids.severity', 'finding_ids.is_resolved')
    def _compute_counts(self):
        for rec in self:
            findings = rec.finding_ids
            rec.critical_count = len(findings.filtered(lambda f: f.severity == 'critical'))
            rec.warning_count = len(findings.filtered(lambda f: f.severity == 'warning'))
            rec.info_count = len(findings.filtered(lambda f: f.severity == 'info'))
            rec.total_count = len(findings)
            rec.unresolved_count = len(findings.filtered(lambda f: not f.is_resolved))

    # ------------------------------------------------------------------------
    # Public entry points
    # ------------------------------------------------------------------------

    def action_run(self):
        """Queue this run to execute in the background and return immediately.

        The audit can take 30s-2min on a large site, well past Odoo's
        HTTP request timeout. We flip a pending flag, fire a one-shot
        cron via `_trigger()`, and let the form's polling widget land
        the user on the completed run as soon as the cron clears the
        flag.

        The legacy synchronous path is still callable via
        ``_run_audit()`` directly (the weekly cron uses it).
        """
        for rec in self:
            rec._queue_audit_run()
        # Return a soft_reload so the form immediately re-renders with
        # the new `is_audit_pending=True` state — the Run button hides,
        # the spinner banner appears, the poll widget starts ticking.
        return {'type': 'ir.actions.client', 'tag': 'soft_reload'}

    def _queue_audit_run(self):
        """Flip the ICP audit-pending flag, stamp the run id, fire the cron.

        Shared by the hub button, the run form's Run/Re-run, and the
        wizard. The audit-pending state ICPs:

          - era_seo.audit_pending              bool, gate the poll widget
          - era_seo.audit_pending_started_at   timestamp, TTL safety net
          - era_seo.audit_pending_run_id       id, what the cron will scan
          - era_seo.audit_pending_user_id      id, sudo'd-as for the audit
        """
        self.ensure_one()
        ICP = self.env['ir.config_parameter'].sudo()
        ICP.set_param('era_seo.audit_pending', 'True')
        ICP.set_param(
            'era_seo.audit_pending_started_at',
            fields.Datetime.to_string(fields.Datetime.now()),
        )
        ICP.set_param('era_seo.audit_pending_run_id', str(self.id))
        ICP.set_param('era_seo.audit_pending_user_id', str(self.env.user.id))
        cron = self.env.ref(
            'era_seo_suite.cron_run_audit_async', raise_if_not_found=False)
        if cron:
            try:
                cron.sudo()._trigger()
            except Exception:  # noqa: BLE001
                _logger.exception('queue_audit_run: cron._trigger failed')
        # Hint the form to re-fetch is_audit_pending right away.
        self.env['era.seo.suite.hub'].sudo().invalidate_model(['is_audit_pending'])

    @api.model
    def cron_run_audit_async(self):
        """Cron entry — execute whichever run is queued in
        ``era_seo.audit_pending_run_id``, then clear the flag.

        Wraps `_run_audit()` so the pending flag is always cleared on
        the way out — success, failure, AI provider crash, anything.
        The poll widget detects pending=False and reloads the form.
        """
        ICP = self.env['ir.config_parameter'].sudo()
        if ICP.get_param('era_seo.audit_pending') not in _TRUE:
            return False
        try:
            run_id = int(ICP.get_param('era_seo.audit_pending_run_id', '0') or 0)
        except (TypeError, ValueError):
            run_id = 0
        if not run_id:
            self._clear_audit_pending()
            return False
        run = self.sudo().browse(run_id)
        if not run.exists():
            self._clear_audit_pending()
            return False
        try:
            try:
                user_id = int(ICP.get_param('era_seo.audit_pending_user_id', '0') or 0)
            except (TypeError, ValueError):
                user_id = 0
            ctx_run = run.with_user(user_id) if user_id else run.sudo()
            ctx_run._run_audit()
        finally:
            self._clear_audit_pending()
        return True

    @api.model
    def _clear_audit_pending(self):
        ICP = self.env['ir.config_parameter'].sudo()
        ICP.set_param('era_seo.audit_pending', 'False')
        ICP.set_param('era_seo.audit_pending_started_at', '')
        ICP.set_param('era_seo.audit_pending_run_id', '')
        ICP.set_param('era_seo.audit_pending_user_id', '')

    # ------------------------------------------------------------------
    # Polling RPC + TTL safety-net for the OWL spinner widget.
    # Same shape as era.seo.suite.hub.get_article_pending_state — reads
    # ICP directly to sidestep field-read caching, includes a savepoint
    # so a concurrent cron-clear doesn't poison the request.
    # ------------------------------------------------------------------

    # Most audits take 15-60s on small/medium sites; 5 minutes is plenty
    # of headroom even for slow filesystem queries on large sites.
    _AUDIT_PENDING_TTL_SECONDS = 5 * 60

    @api.model
    def get_audit_pending_state(self):
        """Lightweight RPC for the OWL polling widget."""
        ICP = self.env['ir.config_parameter'].sudo()
        pending = ICP.get_param('era_seo.audit_pending') in _TRUE
        if pending:
            stamp_raw = ICP.get_param('era_seo.audit_pending_started_at') or ''
            stamp = fields.Datetime.from_string(stamp_raw) if stamp_raw else None
            if stamp is None:
                try:
                    with self.env.cr.savepoint():
                        ICP.set_param(
                            'era_seo.audit_pending_started_at',
                            fields.Datetime.to_string(fields.Datetime.now()),
                        )
                except Exception:  # noqa: BLE001
                    pass
            else:
                age = (fields.Datetime.now() - stamp).total_seconds()
                if age > self._AUDIT_PENDING_TTL_SECONDS:
                    _logger.warning(
                        'audit_pending stuck True for %ds (> %ds TTL); '
                        'clearing — the cron either never ran or died '
                        'mid-audit. Re-run from the audit list.',
                        int(age), self._AUDIT_PENDING_TTL_SECONDS)
                    try:
                        with self.env.cr.savepoint():
                            self._clear_audit_pending()
                    except Exception:  # noqa: BLE001
                        pass
                    pending = False
        return {'pending': pending}

    @api.model
    def run_scheduled_audit(self, website_id=None):
        """Cron entry: create a fresh run and execute it. Returns the run.

        Used by the weekly cron, which already runs in the background,
        so we keep this path synchronous (the cron worker IS the background).
        """
        vals = {'state': 'draft'}
        if website_id:
            vals['website_id'] = website_id
        run = self.create(vals)
        run._run_audit()
        return run

    # ------------------------------------------------------------------
    # Retention / cleanup
    # ------------------------------------------------------------------
    #
    # Each scan creates a fresh era.seo.audit.run row. The findings, NOT
    # the run, are the long-lived data — findings are upserted by
    # (check_code, res_model, res_id, lang_id) so deleting an old run
    # never deletes the defects it discovered (they have
    # `ondelete='set null'` on run_id and stay queryable). The run row
    # itself is just a "scan session" record carrying:
    #   - when the scan happened (date_started/date_finished)
    #   - which website was scoped
    #   - the per-run counts (denormalised for the list view)
    #
    # After ~90 days the per-run counts stop being useful (the dashboard
    # only cares about the latest run, and there's no trend/history view).
    # Keeping unbounded run history is just visual noise in the list.

    _DEFAULT_RUN_RETENTION_DAYS = 90

    @api.model
    def action_cleanup_old_runs(self):
        """Delete runs older than ``era_seo.run_retention_days`` ICP days.

        Findings whose ``run_id`` pointed at a deleted run keep their AI
        state (status/proposals/explanations) — ``run_id`` has
        ``ondelete='set null'`` on era.seo.audit.finding so they orphan
        cleanly. Their "Last detected in" column reads empty after that,
        which is the honest answer.

        Returns a notification action with the count for the manual button.
        Cron callers ignore the return value.
        """
        ICP = self.env['ir.config_parameter'].sudo()
        try:
            days = int(ICP.get_param(
                'era_seo.run_retention_days',
                str(self._DEFAULT_RUN_RETENTION_DAYS)))
        except (TypeError, ValueError):
            days = self._DEFAULT_RUN_RETENTION_DAYS
        if days <= 0:
            return self._cleanup_notify(
                'info', _('Run retention is disabled (days = 0).'))

        from datetime import timedelta
        cutoff = fields.Datetime.now() - timedelta(days=days)
        old_runs = self.sudo().search([('date_started', '<', cutoff)])
        count = len(old_runs)
        if not count:
            return self._cleanup_notify(
                'info',
                _('No audit runs older than %d days to clean up.', days))
        old_runs.unlink()
        _logger.info(
            'audit-run cleanup: deleted %d run(s) older than %d days', count, days)
        return self._cleanup_notify(
            'success',
            _('Deleted %d audit run(s) older than %d days. '
              'The defects they found remain in Findings.', count, days))

    @api.model
    def cron_cleanup_old_runs(self):
        """Daily cron entry. Gated by ``era_seo.run_retention_active`` ICP
        so admins can pause it without disabling the cron record.
        """
        ICP = self.env['ir.config_parameter'].sudo()
        active = ICP.get_param('era_seo.run_retention_active', 'True')
        if active not in ('True', '1', 'true', 'yes', 'on'):
            return
        self.action_cleanup_old_runs()

    @staticmethod
    def _cleanup_notify(kind, message):
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'type': kind,
                'message': message,
                'sticky': False,
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }

    def _run_audit(self):
        """Execute every ``_check_*`` method on this model against ``self``.

        Wrapped in a savepoint so a single check failing doesn't abort the
        rest. Each check is a Python method that creates 0..n finding rows
        on ``self``.
        """
        self.ensure_one()
        self.write({
            'state': 'running',
            'date_started': fields.Datetime.now(),
            'pages_scanned': 0,
            'error_message': False,
        })

        # Track the (check_code, res_model, res_id, lang_id) keys detected
        # this run so we can (a) upsert instead of duplicating and (b)
        # auto-resolve findings on scanned pages that no longer occur.
        _run_local.seen_finding_keys = set()
        _run_local.audit_languages = None  # recomputed per run
        _run_local.rendered_cache = {}  # {page_id: rendered #wrap doc or None}

        try:
            pages = self._scope_pages()
            self.write({'pages_scanned': len(pages)})
            from psycopg2 import errors as _pg_errors
            for check_method in self._get_check_methods():
                # Per-check retry loop for SerializationFailure: postgres
                # rejects one of any two concurrent transactions touching
                # the same rows, and an audit running alongside an AI
                # fill_seo (or another worker writing translations) loses
                # these races regularly. Catching the error in a per-check
                # savepoint and moving on used to mean the check was
                # silently abandoned — the run looked "complete" with
                # missing findings. Retry up to 3 times with short backoff
                # before giving up.
                attempt = 0
                while True:
                    attempt += 1
                    try:
                        with self.env.cr.savepoint():
                            check_method(pages)
                        break
                    except _pg_errors.SerializationFailure as exc:
                        if attempt < 3:
                            import time as _time
                            _time.sleep(0.2 * attempt)
                            continue
                        _logger.warning(
                            'audit.run %d: check %s abandoned after %d '
                            'serialization retries — concurrent writes on '
                            'the same rows prevented this check from '
                            'completing. The next audit run will pick it up.',
                            self.id, check_method.__name__, attempt,
                        )
                        break
                    except Exception as exc:  # noqa: BLE001
                        _logger.warning(
                            'audit.run %d: check %s failed: %s',
                            self.id, check_method.__name__, exc,
                        )
                        break
            self._auto_resolve_fixed(pages)
            self.write({
                'state': 'done',
                'date_finished': fields.Datetime.now(),
            })
        except Exception as exc:  # noqa: BLE001
            _logger.exception('audit.run %d: aborted with error', self.id)
            self.write({
                'state': 'failed',
                'date_finished': fields.Datetime.now(),
                'error_message': str(exc),
            })

    def _auto_resolve_fixed(self, pages):
        """Mark unresolved findings on scanned pages that were NOT re-detected.

        If a page was scanned this run and a previously-open finding for it
        is no longer in ``_seen_finding_keys``, the issue has been fixed —
        resolve it (preserving history) rather than leaving a stale row.
        Scoped to the pages actually scanned so other websites / unscanned
        records are never touched.
        """
        Finding = self.env['era.seo.audit.finding'].sudo()
        if not pages:
            return
        open_findings = Finding.search([
            ('is_resolved', '=', False),
            ('res_model', '=', 'website.page'),
            ('res_id', 'in', pages.ids),
        ])
        seen = getattr(_run_local, 'seen_finding_keys', set())
        stale = open_findings.filtered(
            lambda f: (f.check_code, f.res_model, f.res_id,
                       f.lang_id.id if f.lang_id else False) not in seen
        )
        if stale:
            stale.write({
                'is_resolved': True,
                'resolved_date': fields.Datetime.now(),
                'resolved_user_id': self.env.user.id,
                'resolved_manually': False,   # auto: reopens if re-detected later
            })

    # ------------------------------------------------------------------------
    # Scoping + check registry
    # ------------------------------------------------------------------------

    def _scope_pages(self):
        """Return the recordset of pages to audit (honours website_id scope).

        Collapses Odoo copy-on-write duplicates: a generic page
        (``website_id`` unset) and a website-specific page can share the same
        URL, but only the specific one is actually served — the generic is
        shadowed. Without this the audit reports the same URL twice (e.g. two
        'Thin Content' findings for /contactus). Per URL we keep the
        website-specific page(s) when any exist and drop the generic; if a URL
        has only generic pages we keep a single one.
        """
        Page = self.env['website.page'].sudo()
        domain = []
        if self.website_id:
            domain.append(('website_id', '=', self.website_id.id))
        pages = Page.search(domain)

        from collections import defaultdict
        by_url = defaultdict(list)
        for p in pages:
            by_url[(p.url or '').rstrip('/') or '/'].append(p)

        keep_ids = []
        for _url, group in by_url.items():
            specific = [p for p in group if p.website_id]
            keep = specific if specific else group[:1]
            keep_ids.extend(p.id for p in keep)
        return Page.browse(keep_ids)

    def _get_check_methods(self):
        """List the bound ``_check_*`` methods to run, in registration order."""
        names = [
            '_check_missing_seo_title',
            '_check_title_length',
            '_check_duplicate_seo_title',
            '_check_missing_meta_description',
            '_check_description_length',
            '_check_duplicate_meta_description',
            '_check_missing_og_image',
            '_check_missing_canonical',
            '_check_noindex_in_sitemap',
            '_check_missing_h1',
            '_check_multiple_h1',
            '_check_image_missing_alt',
            '_check_slug_length',
            '_check_slug_uppercase',
            '_check_slug_stopwords',
            '_check_missing_schema',
            '_check_thin_content',
            '_check_orphan_page',
            '_check_redirect_chain',
            '_check_redirect_loop',
        ]
        return [getattr(self, name) for name in names if hasattr(self, name)]

    # ------------------------------------------------------------------------
    # Finding helper
    # ------------------------------------------------------------------------

    def _add_finding(self, page, severity, code, name, details=None,
                     suggested=None, lang=None):
        """Upsert one finding keyed by (check_code, res_model, res_id).

        Re-detecting the same defect updates the existing row (refreshes
        severity/name/details, re-points run_id at this run, and reopens it
        if it had been resolved) instead of creating a duplicate. Only
        genuinely new defects create a new row. AI-fill fields on the
        existing finding are preserved.
        """
        Finding = self.env['era.seo.audit.finding'].sudo()
        lang_id = lang.id if lang else False
        # Remember we saw this key so _auto_resolve_fixed leaves it alone.
        seen = getattr(_run_local, 'seen_finding_keys', None)
        if seen is not None:
            seen.add((code, page._name, page.id, lang_id))

        vals = {
            'run_id': self.id,
            'severity': severity,
            'check_name': name,
            'url': page.url or '/',
            'details': details or '',
            'suggested_fix': suggested or '',
        }
        existing = Finding.search([
            ('check_code', '=', code),
            ('res_model', '=', page._name),
            ('res_id', '=', page.id),
            ('lang_id', '=', lang_id),
        ], limit=1)
        if existing:
            # Reopen ONLY if it was auto-resolved (a clean past audit). A finding
            # the user MANUALLY marked resolved (resolved_manually) is a
            # deliberate dismissal — re-running the audit must NOT undo it, even
            # if the defect is still technically present. The AI status
            # ('applied'/'suggested') is preserved across audits so re-running
            # never wipes the record of fixes already attempted.
            if existing.is_resolved and not existing.resolved_manually:
                vals.update({
                    'is_resolved': False,
                    'resolved_date': False,
                    'resolved_user_id': False,
                })
            existing.write(vals)
        else:
            vals.update({
                'check_code': code,
                'res_model': page._name,
                'res_id': page.id,
                'lang_id': lang_id,
            })
            Finding.create(vals)

    # ------------------------------------------------------------------------
    # Checks — title / description
    # ------------------------------------------------------------------------

    def _audit_languages(self, pages):
        """Languages to check for per-language content defects.

        Cached on _run_local for the duration of the run. Prefers the run's
        website languages; falls back to the union across the scanned pages'
        websites, then to every active language.
        """
        cached = getattr(_run_local, 'audit_languages', None)
        if cached is not None:
            return cached
        Lang = self.env['res.lang']
        if self.website_id:
            langs = self.website_id.language_ids
        else:
            langs = pages.mapped('website_id.language_ids')
        if not langs:
            langs = Lang.search([('active', '=', True)])
        _run_local.audit_languages = langs
        return langs

    def _check_missing_seo_title(self, pages):
        for lang in self._audit_languages(pages):
            for p in pages:
                if not p.with_context(lang=lang.code).seo_title:
                    self._add_finding(
                        p, 'critical', 'missing_seo_title',
                        'Missing SEO Title [{}]'.format(lang.code),
                        details='No seo_title in {}; falls back to website name.'.format(lang.code),
                        suggested='Add a concise (~50 char) title for {}.'.format(lang.code),
                        lang=lang,
                    )

    def _check_title_length(self, pages):
        for lang in self._audit_languages(pages):
            for p in pages:
                title = p.with_context(lang=lang.code).seo_title
                if not title:
                    continue
                n = len(title)
                if n > _TITLE_TOO_LONG:
                    self._add_finding(
                        p, 'warning', 'title_too_long',
                        'SEO Title Too Long ({} chars) [{}]'.format(n, lang.code),
                        details='SERP truncates beyond ~60 chars.',
                        suggested='Shorten the title to {} chars or fewer.'.format(_TITLE_TOO_LONG),
                        lang=lang,
                    )
                elif n < _TITLE_TOO_SHORT:
                    self._add_finding(
                        p, 'info', 'title_too_short',
                        'SEO Title Too Short ({} chars) [{}]'.format(n, lang.code),
                        details='Short titles miss keyword opportunities.',
                        suggested='Expand the title toward 50-60 chars.',
                        lang=lang,
                    )

    def _check_duplicate_seo_title(self, pages):
        for lang in self._audit_languages(pages):
            groups = defaultdict(list)
            for p in pages:
                title = p.with_context(lang=lang.code).seo_title
                if title:
                    groups[title].append(p)
            for title, dupes in groups.items():
                if len(dupes) > 1:
                    for p in dupes:
                        others = ', '.join(d.url or '/' for d in dupes if d != p)
                        self._add_finding(
                            p, 'critical', 'duplicate_seo_title',
                            'Duplicate SEO Title [{}]'.format(lang.code),
                            details='Same {} title also on: {}'.format(lang.code, others),
                            suggested='Make each page title unique.',
                            lang=lang,
                        )

    def _check_missing_meta_description(self, pages):
        for lang in self._audit_languages(pages):
            for p in pages:
                if not p.with_context(lang=lang.code).seo_description:
                    self._add_finding(
                        p, 'critical', 'missing_meta_description',
                        'Missing Meta Description [{}]'.format(lang.code),
                        suggested='Add a 140-160 char description for {}.'.format(lang.code),
                        lang=lang,
                    )

    def _check_description_length(self, pages):
        for lang in self._audit_languages(pages):
            for p in pages:
                desc = p.with_context(lang=lang.code).seo_description
                if not desc:
                    continue
                n = len(desc)
                if n > _DESC_TOO_LONG:
                    self._add_finding(
                        p, 'warning', 'description_too_long',
                        'Meta Description Too Long ({} chars) [{}]'.format(n, lang.code),
                        suggested='Trim to {} chars or fewer.'.format(_DESC_TOO_LONG),
                        lang=lang,
                    )
                elif n < _DESC_TOO_SHORT:
                    self._add_finding(
                        p, 'info', 'description_too_short',
                        'Meta Description Too Short ({} chars) [{}]'.format(n, lang.code),
                        suggested='Expand toward 140-160 chars.',
                        lang=lang,
                    )

    def _check_duplicate_meta_description(self, pages):
        for lang in self._audit_languages(pages):
            groups = defaultdict(list)
            for p in pages:
                desc = p.with_context(lang=lang.code).seo_description
                if desc:
                    groups[desc].append(p)
            for desc, dupes in groups.items():
                if len(dupes) > 1:
                    for p in dupes:
                        others = ', '.join(d.url or '/' for d in dupes if d != p)
                        self._add_finding(
                            p, 'warning', 'duplicate_meta_description',
                            'Duplicate Meta Description [{}]'.format(lang.code),
                            details='Same {} description also on: {}'.format(lang.code, others),
                            suggested='Make each description unique.',
                            lang=lang,
                        )

    # ------------------------------------------------------------------------
    # Checks — OG / canonical / indexing
    # ------------------------------------------------------------------------

    def _check_missing_og_image(self, pages):
        for p in pages.filtered(lambda r: not (r.seo_og_image or r.seo_og_image_url)):
            self._add_finding(
                p, 'warning', 'missing_og_image', 'Missing OG Image',
                suggested='Upload an OG image (recommended 1200x630) in the SEO tab.',
            )

    def _check_missing_canonical(self, pages):
        # The mixin's get_seo_url() always returns SOMETHING, so the only
        # meaningful "missing" case is when the configured base_url is empty.
        base = self.env['ir.config_parameter'].sudo().get_param('web.base.url', '')
        if base:
            return
        for p in pages:
            if not p.seo_canonical_url:
                self._add_finding(
                    p, 'info', 'missing_canonical', 'Cannot Resolve Canonical',
                    details='web.base.url is empty and no canonical override is set.',
                    suggested='Set web.base.url in system parameters.',
                )

    def _check_noindex_in_sitemap(self, pages):
        for p in pages:
            if p.seo_sitemap_include and not p.seo_robots_index:
                self._add_finding(
                    p, 'critical', 'noindex_in_sitemap',
                    'Noindex Page Included in Sitemap',
                    details='Page is set to noindex but is_sitemap_include is True.',
                    suggested='Either re-enable indexing or exclude from sitemap.',
                )

    # ------------------------------------------------------------------------
    # Checks — content HTML
    # ------------------------------------------------------------------------

    @staticmethod
    def _meaningful_h1s(doc):
        """H1 elements that are real headings — i.e. have non-empty text or wrap
        an image (logo-style heading). Snippets commonly leave EMPTY <h1></h1>
        placeholders in the markup; those aren't competing headings and must not
        count toward 'multiple H1' (nor satisfy 'missing H1'). This stops the
        false 'Multiple <h1> Tags (3)' on pages a user sees only one heading on."""
        return [h for h in doc.xpath('//h1')
                if (h.text_content() or '').strip() or h.xpath('.//img')]

    def _check_missing_h1(self, pages):
        for p in pages:
            doc = self._dom_doc(p)
            if doc is None:
                continue
            if not self._meaningful_h1s(doc):
                self._add_finding(
                    p, 'critical', 'missing_h1', 'Missing <h1>',
                    suggested='Add exactly one H1 to the page content.',
                )

    def _check_multiple_h1(self, pages):
        for p in pages:
            doc = self._dom_doc(p)
            if doc is None:
                continue
            h1s = self._meaningful_h1s(doc)
            if len(h1s) > 1:
                self._add_finding(
                    p, 'warning', 'multiple_h1',
                    'Multiple <h1> Tags ({})'.format(len(h1s)),
                    suggested='Keep only one H1; convert the extra heading(s) to H2.',
                )

    def _check_image_missing_alt(self, pages):
        for p in pages:
            doc = self._dom_doc(p)
            if doc is None:
                continue
            imgs = doc.xpath('//img')
            missing = [img for img in imgs if not (img.get('alt') or '').strip()]
            if missing:
                self._add_finding(
                    p, 'warning', 'image_missing_alt',
                    '{} Image(s) Without alt'.format(len(missing)),
                    details='{} of {} <img> elements have no alt attribute.'.format(
                        len(missing), len(imgs)),
                    suggested='Add descriptive alt text to every image.',
                )

    @staticmethod
    def _visible_word_count(doc):
        """Crawlable word count for a content block.

        Joins text NODES with spaces rather than using text_content(), which
        concatenates adjacent block elements with no separator and so merges the
        last word of one block with the first of the next (e.g. a heading ending
        '...كنز؟' immediately followed by a paragraph 'اكتشف...' counted as the
        single token 'كنز؟اكتشف'). That undercounted every page; this counts the
        words a reader/crawler actually sees."""
        if doc is None:
            return 0
        text = ' '.join(t for t in doc.itertext() if t and t.strip())
        return len(text.split())

    def _check_thin_content(self, pages):
        # Threshold is tunable (era_seo.thin_content_words) so sites with
        # intentionally short landing/hub pages can dial it without code.
        raw = self.env['ir.config_parameter'].sudo().get_param(
            'era_seo.thin_content_words')
        try:
            threshold = int(raw) if raw else _THIN_CONTENT_WORDS
        except (TypeError, ValueError):
            threshold = _THIN_CONTENT_WORDS
        for p in pages:
            words = self._visible_word_count(self._dom_doc(p))
            if 0 < words < threshold:
                self._add_finding(
                    p, 'warning', 'thin_content',
                    'Thin Content ({} words)'.format(words),
                    details='Pages with fewer than {} words tend to rank poorly.'.format(
                        threshold),
                    suggested='Expand the content with more depth on the topic.',
                )

    # ------------------------------------------------------------------------
    # Checks — slug
    # ------------------------------------------------------------------------

    def _check_slug_length(self, pages):
        for p in pages:
            slug = (p.url or '').strip('/').split('/')[-1]
            if len(slug) > _SLUG_TOO_LONG:
                self._add_finding(
                    p, 'info', 'slug_too_long',
                    'Slug Too Long ({} chars)'.format(len(slug)),
                    suggested='Shorten the URL slug below {} chars.'.format(_SLUG_TOO_LONG),
                )

    def _check_slug_uppercase(self, pages):
        for p in pages:
            slug = (p.url or '').strip('/').split('/')[-1]
            if any(c.isupper() for c in slug):
                self._add_finding(
                    p, 'info', 'slug_contains_uppercase',
                    'Slug Contains Uppercase',
                    suggested='Use lowercase letters and hyphens in URL slugs.',
                )

    def _check_slug_stopwords(self, pages):
        stopwords = _SLUG_STOPWORDS['en'] | _SLUG_STOPWORDS.get('ar', set())
        for p in pages:
            slug = (p.url or '').strip('/').split('/')[-1].lower()
            tokens = re.split(r'[\-_/]+', slug)
            hit = [t for t in tokens if t in stopwords]
            if hit:
                self._add_finding(
                    p, 'info', 'slug_contains_stopwords',
                    'Slug Contains Stopwords ({})'.format(', '.join(hit)),
                    suggested='Drop stopwords from the slug to keep it lean.',
                )

    # ------------------------------------------------------------------------
    # Checks — schema / internal links / redirects
    # ------------------------------------------------------------------------

    def _check_missing_schema(self, pages):
        if 'era.seo.schema.instance' not in self.env:
            return
        Instance = self.env['era.seo.schema.instance'].sudo()
        for p in pages:
            n = Instance.search_count([
                ('res_model', '=', p._name),
                ('res_id', '=', p.id),
                ('active', '=', True),
            ])
            if n == 0:
                self._add_finding(
                    p, 'warning', 'missing_schema', 'No JSON-LD Schema Attached',
                    suggested='Attach an Article / Service / Product schema instance '
                              'from the page form\'s Schemas tab.',
                )

    def _check_orphan_page(self, pages):
        """A page is an orphan when no other page's content links to it.

        Cheap implementation: scan every page's content for href="<this.url>".
        """
        urls_by_id = {p.id: (p.url or '').strip() for p in pages if (p.url or '').strip()}
        # Build content index once.
        contents = {p.id: (p.arch or '') for p in pages}
        for p in pages:
            url = urls_by_id.get(p.id)
            if not url or url == '/':
                # Homepage cannot be an orphan; skip.
                continue
            referenced = False
            for other_id, content in contents.items():
                if other_id == p.id:
                    continue
                if url in content:
                    referenced = True
                    break
            if not referenced:
                self._add_finding(
                    p, 'warning', 'orphan_page', 'Orphan Page',
                    details='No other audited page contains a link to {}.'.format(url),
                    suggested='Link to this page from a relevant navigation or related '
                              'content block so crawlers can discover it.',
                )

    def _check_redirect_chain(self, pages):
        if 'era.seo.redirect' not in self.env:
            return
        Redirect = self.env['era.seo.redirect'].sudo()
        active = Redirect.search([('is_active', '=', True), ('is_regex', '=', False)])
        by_source = {r.source_url: r for r in active}
        for r in active:
            chain = [r.source_url]
            current = r
            seen = {r.source_url}
            depth = 0
            while current and current.target_url in by_source:
                next_r = by_source[current.target_url]
                if next_r.source_url in seen:
                    break  # loop, caught by _check_redirect_loop
                chain.append(next_r.source_url)
                seen.add(next_r.source_url)
                current = next_r
                depth += 1
                if depth > _REDIRECT_CHAIN_MAX:
                    break
            if depth > _REDIRECT_CHAIN_MAX:
                self._add_finding(
                    r, 'critical', 'broken_redirect_chain',
                    'Redirect Chain Too Long ({} hops)'.format(depth),
                    details='Chain: {}'.format(' -> '.join(chain)),
                    suggested='Collapse the chain so each redirect points to the final URL.',
                )

    def _check_redirect_loop(self, pages):
        if 'era.seo.redirect' not in self.env:
            return
        Redirect = self.env['era.seo.redirect'].sudo()
        active = Redirect.search([('is_active', '=', True), ('is_regex', '=', False)])
        by_source = {r.source_url: r for r in active}
        for r in active:
            seen = {r.source_url}
            current = r
            while current and current.target_url in by_source:
                next_r = by_source[current.target_url]
                if next_r.source_url in seen:
                    self._add_finding(
                        r, 'critical', 'redirect_loop', 'Redirect Loop Detected',
                        details='Loop: {} -> ... -> {}'.format(
                            r.source_url, next_r.source_url),
                        suggested='Remove the cycle. The dispatch hook returns 508 '
                                  'when this hits visitors.',
                    )
                    break
                seen.add(next_r.source_url)
                current = next_r

    # ------------------------------------------------------------------------
    # Content extraction helpers
    # ------------------------------------------------------------------------

    def _rendered_wrap_doc(self, page):
        """Fetch the page's rendered public HTML and return the lxml element
        for its content area (``#wrap``), or None.

        The DOM checks (H1, image alt, thin content) must reflect what a
        crawler actually sees. Many pages render their ``<h1>`` from a theme /
        layout snippet that is NOT present in the stored view arch, so parsing
        the arch alone false-flagged 'missing H1' on fully-built pages. We GET
        the page's public URL (the site's default language is served at the
        bare path) and scope to ``#wrap`` so the global header/footer chrome
        doesn't skew element counts. The result (doc or None) is cached on
        ``_run_local`` for the run, so each page is fetched at most once across
        all checks. Any failure (unpublished, timeout, non-200) returns None,
        and the caller falls back to stored-arch parsing.
        """
        cache = getattr(_run_local, 'rendered_cache', None)
        if cache is None:
            cache = {}
            _run_local.rendered_cache = cache
        if page.id in cache:
            return cache[page.id]
        doc = None
        try:
            url = page.url or ''
            published = getattr(page, 'website_published', None)
            if published is None:
                published = getattr(page, 'is_published', True)
            if published and url.startswith('/'):
                base = (self.env['ir.config_parameter'].sudo()
                        .get_param('web.base.url') or '').rstrip('/')
                if base:
                    import requests
                    resp = requests.get(
                        base + url, timeout=_RENDER_FETCH_TIMEOUT_S,
                        headers={'User-Agent': 'ERA-SEO-Audit/1.0'})
                    if resp.status_code == 200 and resp.text:
                        full = lxml_html.fromstring(resp.text)
                        wraps = full.xpath('//*[@id="wrap"]')
                        doc = wraps[0] if wraps else full
        except Exception:  # noqa: BLE001
            doc = None
        cache[page.id] = doc
        return doc

    def _dom_doc(self, page):
        """DOM source for the content checks: the RENDERED page when reachable
        (sees theme/layout-rendered H1s), otherwise the stored language-aware
        arch as a degraded fallback."""
        doc = self._rendered_wrap_doc(page)
        if doc is not None:
            return doc
        return self._content_doc(page)

    @staticmethod
    def _content_doc(page):
        """Return an lxml fragment for the page's content, or None.

        Multilang sites store page content PER LANGUAGE. On an Arabic-primary
        site the en_US arch is frequently an empty layout shell
        (``<div id="wrap" class="oe_empty"/>``) while the real content — and
        its ``<h1>`` — lives in ar_001. Reading ``page.arch`` in the cron's
        default language (en_US) therefore false-flagged 'missing H1' / 'thin
        content' on fully-built pages. We pick the RICHEST language version
        (longest source) across the website's default language and every
        active language, so the DOM checks see the language the page is
        actually authored in.

        Caveat: this still parses the STORED page source. An ``<h1>`` injected
        only by the theme/layout snippet at render time is not visible here —
        that needs a rendered-HTML check (see _check_missing_h1 callers).
        """
        # website.page uses arch_db (delegated through view); blog.post has
        # `content`. Build the candidate language list: website default first,
        # then all active langs, then the context lang as a last resort.
        codes = []
        web = getattr(page, 'website_id', False)
        if web and web.default_lang_id:
            codes.append(web.default_lang_id.code)
        for lang in page.env['res.lang'].sudo().search([]):
            if lang.code not in codes:
                codes.append(lang.code)
        ctx_lang = page.env.context.get('lang')
        if ctx_lang and ctx_lang not in codes:
            codes.append(ctx_lang)
        if not codes:
            codes = [None]

        best = ''
        for code in codes:
            pg = page.with_context(lang=code) if code else page
            html_text = (getattr(pg, 'content', None)
                         or getattr(pg, 'arch', None) or '')
            if len(html_text) > len(best):
                best = html_text
        if not best:
            return None
        try:
            return lxml_html.fragment_fromstring(best, create_parent='div')
        except Exception:  # noqa: BLE001
            return None

    @classmethod
    def _content_text(cls, page):
        doc = cls._content_doc(page)
        if doc is None:
            return ''
        return ' '.join(doc.text_content().split())

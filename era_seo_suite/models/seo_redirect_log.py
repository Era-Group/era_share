"""ERA SEO — 404 log.

Captures unmatched-path requests so admins can convert frequent 404s into
redirects with one click. Records are upserted by (path, website_id) so the
table size stays bounded by the number of distinct missing paths rather than
by total miss volume.

Per SPEC §9.4.
"""
import logging

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


class EraSeoRedirectLog(models.Model):
    """One row per distinct 404 path; counter incremented on every fresh hit."""

    _name = 'era.seo.redirect.log'
    _description = 'SEO 404 Log'
    _order = 'last_seen desc, hit_count desc'

    path = fields.Char(string='Request Path', required=True, index=True)
    website_id = fields.Many2one(
        'website', string='Website', ondelete='cascade', index=True,
    )
    hit_count = fields.Integer(default=1, readonly=True)
    first_seen = fields.Datetime(default=fields.Datetime.now, readonly=True)
    last_seen = fields.Datetime(default=fields.Datetime.now, readonly=True)
    last_referer = fields.Char(
        string='Last Referer',
        readonly=True,
        help='HTTP Referer of the most recent miss. Stripped to host+path; '
             'never stored if the referer equals the page itself (privacy + '
             'log spam).',
    )
    last_user_agent = fields.Char(string='Last User-Agent', readonly=True)
    is_resolved = fields.Boolean(
        default=False,
        help='Set automatically when a matching era.seo.redirect is created.',
    )

    # --- SQL --------------------------------------------------------------

    def init(self):
        """Composite uniqueness on (path, website_id) for fast upsert."""
        self.env.cr.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS era_seo_redirect_log_path_website_uidx
            ON era_seo_redirect_log (path, COALESCE(website_id, 0))
        """)

    # --- Recording API ----------------------------------------------------

    @api.model
    def _record_miss(self, path, website=None, referer=None, user_agent=None):
        """Upsert a 404 log row by ``(path, website)``.

        Always called from a request context where errors must never leak;
        wraps in a savepoint so a logging failure cannot break the original
        404 response.
        """
        if not path:
            return
        try:
            with self.env.cr.savepoint():
                self._do_upsert(path, website, referer, user_agent)
        except Exception as exc:  # noqa: BLE001
            _logger.warning('era.seo.redirect.log: upsert for %r failed: %s', path, exc)

    @api.model
    def _do_upsert(self, path, website, referer, user_agent):
        website_id = website.id if website else False
        clean_referer = self._sanitize_referer(referer, path)
        clean_ua = (user_agent or '')[:255] or False
        # Common path: the row already exists. Increment atomically in-DB
        # (`hit_count = hit_count + 1`) so two concurrent 404s on the same path
        # can't lose an increment via a read-modify-write race — that race was
        # the source of the `UPDATE era_seo_redirect_log ... 40001` errors. The
        # COALESCE on website_id mirrors the unique index expression so a NULL
        # and a 0 website map to the same log row. A new referer/UA only
        # overwrites when present (keeps the last known value otherwise).
        self.env.cr.execute(
            """
            UPDATE era_seo_redirect_log
               SET hit_count = hit_count + 1,
                   last_seen = (now() AT TIME ZONE 'UTC'),
                   last_referer = COALESCE(%s, last_referer),
                   last_user_agent = COALESCE(%s, last_user_agent),
                   write_uid = %s,
                   write_date = (now() AT TIME ZONE 'UTC')
             WHERE path = %s AND COALESCE(website_id, 0) = COALESCE(%s, 0)
            """,
            (clean_referer or None, clean_ua or None, self.env.uid,
             path, website_id or None),
        )
        if self.env.cr.rowcount:
            return
        # No row yet — create via the ORM so the audit columns are populated.
        # A concurrent creator racing us trips the unique index; that raises,
        # and the caller's savepoint in `_record_miss` swallows it (one logged
        # hit lost, never a broken 404 response).
        self.sudo().create({
            'path': path,
            'website_id': website_id,
            'hit_count': 1,
            'first_seen': fields.Datetime.now(),
            'last_seen': fields.Datetime.now(),
            'last_referer': clean_referer,
            'last_user_agent': clean_ua,
        })

    @staticmethod
    def _sanitize_referer(referer, current_path):
        """Drop self-references and trim to 512 chars."""
        if not referer:
            return False
        ref = referer.strip()
        if not ref:
            return False
        # Strip if the referer ends with the same path we're recording.
        if ref.endswith(current_path):
            return False
        return ref[:512]

    # --- Actions ----------------------------------------------------------

    def action_create_redirect(self):
        """One-click: open a Redirect form pre-filled from this log row."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Create Redirect'),
            'res_model': 'era.seo.redirect',
            'view_mode': 'form',
            'target': 'current',
            'context': {
                'default_source_url': self.path,
                'default_website_id': self.website_id.id if self.website_id else False,
                'default_created_from': 'auto_404',
                'default_notes': _('Created from 404 log entry (%d hits).',
                                    self.hit_count),
                'era_seo_log_id': self.id,
            },
        }

    def action_mark_resolved(self):
        self.write({'is_resolved': True})

    # --- Vacuum -----------------------------------------------------------

    @api.model
    def _vacuum_old_entries(self, days=90):
        """Cron entry point: drop resolved/old entries to keep the table bounded.

        Default policy:
          - Any row with ``is_resolved=True`` and last_seen older than 30 days.
          - Any unresolved row with last_seen older than ``days`` (default 90).
        """
        from datetime import timedelta
        from odoo.fields import Datetime as DT
        now = DT.now()
        cutoff_resolved = now - timedelta(days=30)
        cutoff_unresolved = now - timedelta(days=days)
        resolved = self.search([
            ('is_resolved', '=', True),
            ('last_seen', '<', cutoff_resolved),
        ])
        old = self.search([
            ('is_resolved', '=', False),
            ('last_seen', '<', cutoff_unresolved),
        ])
        count = len(resolved) + len(old)
        (resolved + old).unlink()
        if count:
            _logger.info('era.seo.redirect.log: vacuumed %d entries', count)
        return count

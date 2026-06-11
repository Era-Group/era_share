"""ERA GSC — verified GSC property."""
import logging
from datetime import date, timedelta

import requests

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import config

from .gsc_client import GscClient

_logger = logging.getLogger(__name__)


def _icp_int(env, key, default):
    raw = env['ir.config_parameter'].sudo().get_param(key)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


class EraGscSite(models.Model):
    _name = 'era.gsc.site'
    _description = 'GSC Site'
    _order = 'name'

    name = fields.Char(
        string='Site URL', required=True,
        help='GSC site URL — exactly as Google reports it '
             '(e.g. "sc-domain:era.net.sa" or "https://era.net.sa/").',
    )
    account_id = fields.Many2one(
        'era.gsc.account', string='Account',
        required=True, ondelete='cascade', index=True,
    )
    permission_level = fields.Char(string='Permission Level', readonly=True)
    query_ids = fields.One2many('era.gsc.query', 'site_id', string='Queries')
    query_count = fields.Integer(compute='_compute_query_count')
    last_pull_date = fields.Date(string='Last Pulled', readonly=True)
    active = fields.Boolean(default=True)

    _site_unique = models.Constraint(
        'UNIQUE(account_id, name)',
        'A site URL can only exist once per account.',
    )

    @api.depends('query_ids')
    def _compute_query_count(self):
        for rec in self:
            rec.query_count = len(rec.query_ids)

    # ------------------------------------------------------------------

    # GSC data lags ~2 days and keeps finalizing for ~3, so the freshest
    # reliable day is today-2; we anchor every window there.
    _GSC_LAG_DAYS = 2

    def action_pull_now(self):
        """Manual button — refresh the configured medium window (default 28d)."""
        days = _icp_int(self.env, 'era_seo_suite.pull_window_days', 28)
        return self._pull_window(days)

    def action_backfill(self):
        """Manual button — backfill the long history (default 90 days / 3
        months). GSC retains ~16 months; pulled day-by-day so the per-request
        row cap can never truncate it."""
        days = _icp_int(self.env, 'era_seo_suite.gsc_backfill_days', 90)
        return self._pull_window(days)

    def _pull_window(self, days):
        end = date.today() - timedelta(days=self._GSC_LAG_DAYS)
        start = end - timedelta(days=max(1, days) - 1)
        for site in self:
            site._pull(start, end)
        return True

    def _pull(self, start_date, end_date):
        """Upsert era.gsc.query for every DAY in [start, end].

        Pulled one day at a time (not as a single ranged request): GSC caps a
        request's row set, so a multi-day query with dimensions ('date','query')
        silently truncates to the first few days for a busy site. Per-day +
        pagination guarantees the full history. Progress is committed per day so
        a long backfill is durable/resumable.
        """
        self.ensure_one()
        token = self.account_id._get_valid_access_token()
        # Commit per day for durable/resumable progress — but never mid-test
        # (a commit there would break the test transaction's isolation).
        in_test = bool(config['test_enable'])
        total = 0
        day = start_date
        while day <= end_date:
            total += self._pull_one_day(day, token)
            self.sudo().write({'last_pull_date': day})
            if not in_test:
                self.env.cr.commit()  # durable per-day progress
            day += timedelta(days=1)
        _logger.info('GSC: pulled %d rows for %s (%s..%s)',
                     total, self.name, start_date, end_date)
        return total

    def _pull_one_day(self, day, token):
        """Fetch ALL query rows for a single date (paginated) and upsert them.
        Race-safe against a concurrent cron/manual pull via the UNIQUE
        (site,date,query) constraint + a per-create savepoint."""
        self.ensure_one()
        Query = self.env['era.gsc.query'].sudo()
        iso = day.isoformat()
        written = 0
        start_row = 0
        page = 25000  # GSC per-request maximum
        while True:
            try:
                rows = GscClient.search_analytics(
                    token, self.name, iso, iso,
                    dimensions=('query',), row_limit=page, start_row=start_row)
            except requests.HTTPError as exc:
                # 4xx from the Google API is a user-config problem (revoked
                # token, site not in the property, lacking permission).
                # Surface it without a traceback — `last_error` has the detail.
                self.account_id.sudo().write(
                    {'state': 'error', 'last_error': str(exc)[:1000]})
                _logger.warning('GSC search_analytics %s %s: %s',
                                self.name, iso, exc)
                raise UserError(
                    _('GSC pull failed for %s: %s', self.name, exc)) from exc
            except Exception as exc:  # noqa: BLE001
                # Network errors (ConnectionError/Timeout are NOT HTTPError)
                # and anything unexpected must still mark the account so the
                # admin sees the failure instead of a silent stale pull.
                self.account_id.sudo().write(
                    {'state': 'error', 'last_error': str(exc)[:1000]})
                _logger.exception('GSC search_analytics failed for %s %s',
                                  self.name, iso)
                raise UserError(
                    _('GSC pull failed for %s: %s', self.name, exc)) from exc
            if not rows:
                break
            # Pre-load existing rows for this day's queries in one query, so we
            # update in place and only CREATE genuinely new (site,date,query).
            qs = [((r.get('keys') or [''])[0] or '').strip() for r in rows]
            qs = [q for q in qs if q]
            existing = {q.query: q for q in Query.search(
                [('site_id', '=', self.id), ('date', '=', day),
                 ('query', 'in', qs)])}
            for r in rows:
                keys = r.get('keys') or []
                q = ((keys[0] if keys else '') or '').strip()
                if not q:
                    continue
                vals = {
                    'site_id': self.id, 'date': day, 'query': q,
                    'clicks': int(r.get('clicks') or 0),
                    'impressions': int(r.get('impressions') or 0),
                    'ctr': float(r.get('ctr') or 0.0),
                    'position': float(r.get('position') or 0.0),
                }
                rec = existing.get(q)
                if rec:
                    rec.write(vals)
                else:
                    try:
                        with self.env.cr.savepoint():
                            existing[q] = Query.create(vals)
                    except Exception:  # noqa: BLE001 — concurrent insert won
                        dup = Query.search(
                            [('site_id', '=', self.id), ('date', '=', day),
                             ('query', '=', q)], limit=1)
                        if dup:
                            dup.write(vals)
                written += 1
            if len(rows) < page:
                break
            start_row += page
        return written

    # ------------------------------------------------------------------
    # Cron entry
    # ------------------------------------------------------------------

    def cron_pull_all(self):
        """Daily cron — refresh only the most RECENT days (default 2) for every
        connected site. GSC finalizes a day's data over ~3 days, so a small
        rolling window keeps things current cheaply; the long history is filled
        once via the Backfill button. Configurable: era_seo_suite.gsc_daily_days.
        """
        days = _icp_int(self.env, 'era_seo_suite.gsc_daily_days', 2)
        end = date.today() - timedelta(days=self._GSC_LAG_DAYS)
        start = end - timedelta(days=max(1, days) - 1)
        sites = self.sudo().search([
            ('active', '=', True),
            ('account_id.state', '=', 'connected'),
            ('account_id.active', '=', True),
        ])
        for site in sites:
            try:
                site._pull(start, end)
            except Exception as exc:  # noqa: BLE001
                _logger.warning('GSC cron pull failed for %s: %s',
                                site.name, exc)

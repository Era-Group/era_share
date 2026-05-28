"""ERA GSC — verified GSC property."""
import logging
from datetime import date, timedelta

from odoo import _, fields, models
from odoo.exceptions import UserError

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

    def _compute_query_count(self):
        for rec in self:
            rec.query_count = len(rec.query_ids)

    # ------------------------------------------------------------------

    def action_pull_now(self):
        """Pull the configured window of days into era.gsc.query."""
        days = _icp_int(self.env, 'era_seo_suite.pull_window_days', 28)
        end = date.today() - timedelta(days=2)  # GSC data is ~2 days delayed
        start = end - timedelta(days=days - 1)
        for site in self:
            site._pull(start, end)
        return True

    def _pull(self, start_date, end_date):
        """Fetch (date, query) rows in the window and upsert era.gsc.query."""
        self.ensure_one()
        token = self.account_id._get_valid_access_token()
        try:
            rows = GscClient.search_analytics(
                token, self.name,
                start_date.isoformat(), end_date.isoformat(),
                dimensions=('date', 'query'),
                row_limit=5000,
            )
        except Exception as exc:  # noqa: BLE001
            self.account_id.sudo().write({'state': 'error',
                                          'last_error': str(exc)[:1000]})
            _logger.exception('GSC search_analytics failed for %s', self.name)
            raise UserError(
                _('GSC pull failed for %s: %s', self.name, exc)) from exc

        Query = self.env['era.gsc.query'].sudo()
        written = 0
        for row in rows:
            keys = row.get('keys') or []
            if len(keys) < 2:
                continue
            try:
                row_date = fields.Date.from_string(keys[0])
            except Exception:  # noqa: BLE001
                continue
            query = (keys[1] or '').strip()
            if not query:
                continue
            vals = {
                'site_id': self.id,
                'date': row_date,
                'query': query,
                'clicks': int(row.get('clicks') or 0),
                'impressions': int(row.get('impressions') or 0),
                'ctr': float(row.get('ctr') or 0.0),
                'position': float(row.get('position') or 0.0),
            }
            existing = Query.search([
                ('site_id', '=', self.id),
                ('date', '=', row_date),
                ('query', '=', query),
            ], limit=1)
            if existing:
                existing.write(vals)
            else:
                Query.create(vals)
            written += 1
        self.sudo().write({'last_pull_date': end_date})
        _logger.info('GSC: pulled %d rows for %s (%s..%s)',
                     written, self.name, start_date, end_date)
        return written

    # ------------------------------------------------------------------
    # Cron entry
    # ------------------------------------------------------------------

    def cron_pull_all(self):
        """Daily cron entry — pull all active sites under all connected accounts."""
        sites = self.sudo().search([
            ('active', '=', True),
            ('account_id.state', '=', 'connected'),
            ('account_id.active', '=', True),
        ])
        for site in sites:
            try:
                site.action_pull_now()
            except Exception as exc:  # noqa: BLE001
                _logger.warning('GSC cron pull failed for %s: %s',
                                site.name, exc)

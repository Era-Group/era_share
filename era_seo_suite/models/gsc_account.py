"""ERA GSC — connected Google account.

Owns the refresh_token and the cached access_token. Knows how to:

  - Start the OAuth flow (``action_authorize``) — returns an act_url that
    redirects the admin to Google's consent screen.
  - Refresh its access token when expired.
  - Discover GSC properties available to the authorized user
    (``action_discover_sites``).
  - Trigger a Pull on every active site (``action_pull_all``).

The OAuth callback lives in ``controllers/oauth.py``; it writes the tokens
back to this record.
"""
import logging
import uuid
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .gsc_client import GscClient

_logger = logging.getLogger(__name__)


def _icp(env, key, default=''):
    return env['ir.config_parameter'].sudo().get_param(key, default)


class EraGscAccount(models.Model):
    _name = 'era.gsc.account'
    _description = 'GSC Account'
    _order = 'name'

    name = fields.Char(
        string='Name', required=True,
        help='Friendly name for this connection (e.g. "Marketing — main").',
    )
    email = fields.Char(string='Google Email', readonly=True)
    refresh_token = fields.Char(string='Refresh Token', readonly=True,
                                groups='base.group_system')
    access_token = fields.Char(string='Access Token', readonly=True,
                               groups='base.group_system')
    token_expires_at = fields.Datetime(string='Access Token Expires At',
                                       readonly=True)
    state = fields.Selection(
        [('draft', 'Not Connected'),
         ('connected', 'Connected'),
         ('error', 'Error')],
        default='draft', required=True, readonly=True, index=True,
    )
    last_error = fields.Text(string='Last Error', readonly=True)
    oauth_state_nonce = fields.Char(readonly=True, groups='base.group_system')
    site_ids = fields.One2many('era.gsc.site', 'account_id', string='Sites')
    site_count = fields.Integer(compute='_compute_site_count')
    active = fields.Boolean(default=True)

    @api.depends('site_ids')
    def _compute_site_count(self):
        for rec in self:
            rec.site_count = len(rec.site_ids)

    # ------------------------------------------------------------------
    # OAuth flow
    # ------------------------------------------------------------------

    def action_authorize(self):
        """Open Google's consent screen; ``oauth/callback`` writes back tokens."""
        self.ensure_one()
        client_id = _icp(self.env, 'era_seo_suite.client_id')
        if not client_id:
            raise UserError(_(
                'GSC OAuth client id is not configured. Set it under '
                'Settings → ERA SEO — GSC.'))
        redirect_uri = self._oauth_redirect_uri()
        # The state ties the callback back to this record AND defends against
        # CSRF: include a random single-use nonce that the callback verifies.
        nonce = uuid.uuid4().hex
        self.sudo().write({'oauth_state_nonce': nonce})
        state = '%s:%s' % (self.id, nonce)
        url = GscClient.authorize_url(client_id, redirect_uri, state)
        return {
            'type': 'ir.actions.act_url',
            'url': url,
            'target': 'self',
        }

    def _oauth_redirect_uri(self):
        """Absolute URL of the OAuth callback. Must match the OAuth client config."""
        base = _icp(self.env, 'web.base.url') or ''
        return base.rstrip('/') + '/era_gsc/oauth/callback'

    def _write_tokens(self, payload, email=None):
        """Persist a token response. ``payload`` is the JSON from Google."""
        vals = {
            'access_token': payload.get('access_token') or False,
            'state': 'connected',
            'last_error': False,
        }
        if 'refresh_token' in payload and payload['refresh_token']:
            vals['refresh_token'] = payload['refresh_token']
        expires_in = int(payload.get('expires_in') or 0)
        if expires_in:
            vals['token_expires_at'] = fields.Datetime.now() + timedelta(
                seconds=max(60, expires_in - 60))
        if email and not self.email:
            vals['email'] = email
        self.sudo().write(vals)

    # ------------------------------------------------------------------
    # Token management
    # ------------------------------------------------------------------

    def _get_valid_access_token(self):
        """Return a usable access_token, refreshing if expired."""
        self.ensure_one()
        if not self.refresh_token:
            raise UserError(_('Account "%s" is not connected.', self.name))
        now = fields.Datetime.now()
        if self.access_token and self.token_expires_at and self.token_expires_at > now:
            return self.access_token
        client_id = _icp(self.env, 'era_seo_suite.client_id')
        client_secret = _icp(self.env, 'era_seo_suite.client_secret')
        if not (client_id and client_secret):
            raise UserError(_(
                'GSC OAuth client id/secret are not configured.'))
        payload = GscClient.refresh_access_token(
            client_id, client_secret, self.refresh_token)
        self._write_tokens(payload)
        return payload['access_token']

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_discover_sites(self):
        """Fetch the user's GSC properties and upsert era.gsc.site rows."""
        Site = self.env['era.gsc.site'].sudo()
        for account in self:
            try:
                token = account._get_valid_access_token()
                entries = GscClient.list_sites(token)
            except Exception as exc:  # noqa: BLE001
                account.sudo().write({'state': 'error',
                                      'last_error': str(exc)[:1000]})
                _logger.exception('GSC discover_sites failed for %s', account.name)
                continue
            for entry in entries:
                site_url = entry.get('siteUrl')
                if not site_url:
                    continue
                existing = Site.search([
                    ('account_id', '=', account.id),
                    ('name', '=', site_url),
                ], limit=1)
                vals = {
                    'permission_level': entry.get('permissionLevel') or '',
                }
                if existing:
                    existing.write(vals)
                else:
                    vals.update({'account_id': account.id, 'name': site_url})
                    Site.create(vals)
        return True

    def action_pull_all(self):
        """Pull each active site under each account (medium window)."""
        for account in self:
            for site in account.site_ids.filtered('active'):
                try:
                    site.action_pull_now()
                except Exception as exc:  # noqa: BLE001
                    _logger.warning('GSC pull failed for %s: %s', site.name, exc)
        return True

    def action_backfill_all(self):
        """Backfill the long history (~3 months) for each active site."""
        for account in self:
            for site in account.site_ids.filtered('active'):
                try:
                    site.action_backfill()
                except Exception as exc:  # noqa: BLE001
                    _logger.warning('GSC backfill failed for %s: %s', site.name, exc)
        return True

    def action_open_sites(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('GSC Sites'),
            'res_model': 'era.gsc.site',
            'view_mode': 'list,form',
            'domain': [('account_id', '=', self.id)],
            'context': {'default_account_id': self.id},
        }

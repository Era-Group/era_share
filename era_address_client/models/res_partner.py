import requests

from odoo import _, fields, models
from odoo.exceptions import UserError

_DEFAULT_SERVER = 'https://service.era.net.sa'


class ResPartner(models.Model):
    _inherit = 'res.partner'

    x_short_address = fields.Char(
        string='Short Address',
        size=8,
        index=True,
        help='Saudi National Short Address code (e.g. JCHA4298)',
    )

    def action_resolve_short_address(self):
        """
        Button handler — calls the remote era_address_lookup service at
        service.era.net.sa and writes the result to partner fields.

        Server URL is configured in
        Settings → Accounting → Address Lookup Services.
        """
        for partner in self:
            if not partner.x_short_address:
                raise UserError(_('Please enter the short address first.'))

            result = self._fetch_from_server(partner.x_short_address)
            partner.write(partner._map_lookup_result(result))

    def _fetch_from_server(self, short_address: str) -> dict:
        get_param = self.env['ir.config_parameter'].sudo().get_param

        server_url = (
            get_param('era_address_client.server_url', '') or _DEFAULT_SERVER
        ).rstrip('/')

        endpoint = f'{server_url}/api/era/address/lookup'

        payload = {
            'jsonrpc': '2.0',
            'method': 'call',
            'id': 1,
            'params': {
                'short_address': short_address,
            },
        }

        try:
            resp = requests.post(
                endpoint,
                json=payload,
                timeout=15,
                headers={'Content-Type': 'application/json'},
            )
            resp.raise_for_status()
        except requests.Timeout:
            raise UserError(_(
                'Timed out connecting to the address server (%s).\n'
                'Check network connectivity and the server URL in settings.',
                server_url,
            ))
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            if status in (401, 403):
                raise UserError(_('Contact info@era.net.sa to get access'))
            raise UserError(_(
                'Failed to connect to the address server: %s', str(exc)
            ))
        except requests.RequestException as exc:
            raise UserError(_(
                'Failed to connect to the address server: %s', str(exc)
            ))

        body = resp.json()

        if 'error' in body:
            err = body['error']
            raise UserError(_(
                'Server error: %s', err.get('message') or str(err)
            ))

        result = body.get('result', {})

        if not result.get('success'):
            raise UserError(_(
                'Address not found: %s',
                result.get('message', _('Unknown error')),
            ))

        return result.get('data', {})

    def _map_lookup_result(self, result: dict) -> dict:
        """Map a resolved address dict to res.partner field values."""
        vals = {
            'street':  result.get('street', ''),
            'street2': result.get('district', ''),
            'city':    result.get('city', ''),
            'zip':     result.get('postal_code', ''),
        }
        if 'l10n_sa_edi_building_number' in self._fields:
            vals['l10n_sa_edi_building_number'] = result.get('building_number', '')
        if 'l10n_sa_edi_plot_identification' in self._fields:
            vals['l10n_sa_edi_plot_identification'] = result.get('additional_number', '')
        country_code = result.get('country', '')
        if country_code:
            country = self.env['res.country'].search(
                [('code', '=', country_code)], limit=1
            )
            if country:
                vals['country_id'] = country.id
        return vals

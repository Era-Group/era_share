from odoo import models, fields, api, _
from odoo.exceptions import AccessError, ValidationError, UserError
import requests
import json
from odoo.http import request
import logging
_logger = logging.getLogger(__name__)
from odoo import http
import socket
import os



class CompanyInherit(models.Model):
    _inherit = "res.company"

    user = fields.Char(string="UserName")
    password = fields.Char(string="Password")


    def show_popup(self, title, message, sticky=True, type='danger'):
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _(title),
                'message': _(message),
                'type': type,
                'sticky': sticky,
                'next': {
                    'type': 'ir.actions.act_window_close'
                }
            }
        }

    def _get_api_credentials_client(self):
        """Return (user_name, password) for the current company (self).
        Caller should call this on the EMPLOYEE'S company, not env.company.
        """
        self.ensure_one()
        company = self
        mg_user_name = company.user
        mg_user_password = company.password
        _logger.info(
            "🔑 Muqeem credentials | Company: %s (id=%s) | User: %s | Password: %s",
            company.name, company.id, mg_user_name, mg_user_password,
        )
        return mg_user_name, mg_user_password


    def era_call_muqeem(self, data, url_send_muqeem,  user_name, user_password):
        url = 'https://app.era.net.sa/muqeem/call'


        request = http.request if hasattr(http, 'request') else None
        server_domain = request.httprequest.host_url if request else None
        if not server_domain:
            server_domain = os.getenv('ODOO_SERVER_DOMAIN', 'default.domain.com')
        params = {
            'data': data,
            'url_send_muqeem': url_send_muqeem,
            'user_name': user_name,
            'user_password': user_password,
            'server_domain': server_domain,
        }
        _logger.info('Domain: %s', params['server_domain'])

        payload = json.dumps({
            'jsonrpc': '2.0',
            'params': params
        })
        headers = {
            'Content-Type': 'application/json',
        }
        response = requests.post(url, headers=headers, data=payload)

        _logger.info('Muqeem API response status: %s', response.status_code)
        if response.status_code == 200:
            result = response.json()
            _logger.info('Muqeem API raw result: %s', result)

            if result.get('error'):
                _logger.error('Muqeem error: %s', result.get('error'))
                error_data = result.get('error', {})
                error_msg = error_data.get('data', {}).get('message', '') if isinstance(error_data, dict) else str(error_data)
                raise ValidationError(_('Muqeem Error: %s', error_msg or 'Invalid Username or Password or connection timeout'))

            elif result.get('result'):
                response_data = result.get('result')
                _logger.info('Muqeem success result: %s', response_data)
                return response_data
            else:
                _logger.error('Muqeem unexpected response format: %s', result)
                raise ValidationError(_('Unexpected response from Muqeem service. Please try again.'))
        else:
            _logger.error('Muqeem service HTTP error: %s - %s', response.status_code, response.text)
            if response.status_code == 500:
                raise ValidationError(_('Failed to call muqeem service (Server Error)'))
            if response.status_code == 403:
                raise ValidationError(_('Please check your subscription with Era Group info@era.net.sa'))
            raise ValidationError(_('Failed to call muqeem service (HTTP %s)', response.status_code))
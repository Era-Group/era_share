import json
import logging
import os

import requests
from odoo import _, fields, models
from odoo import http
from odoo.exceptions import ValidationError, UserError

_logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 60

class CompanyInherit(models.Model):
    _inherit = "res.company"

    user = fields.Char(string="UserName")
    password = fields.Char(string="Password")


    def show_popup(self, title, message, sticky=True, type='danger'):
        title_text = title if isinstance(title, str) else str(title or "")
        if isinstance(message, str):
            message_text = message
        elif message is None:
            message_text = ""
        else:
            message_text = json.dumps(message, ensure_ascii=False)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': title_text,
                'message': message_text,
                'type': type,
                'sticky': sticky,
                'next': {
                    'type': 'ir.actions.act_window_close'
                }
            }
        }

    def _get_api_credentials_client(self):
        current_company = self.env.company
        mg_user_name = current_company.user
        mg_user_password = current_company.password

        if not mg_user_name or not mg_user_password:
            raise ValidationError(_("Missing Muqeem username or password in company configuration."))

        return mg_user_name, mg_user_password

    def era_call_muqeem(self, data, url_send_muqeem, user_name, user_password):
        url = 'https://app.era.net.sa/muqeem/call'
        http_request = http.request if hasattr(http, 'request') else None
        server_domain = http_request.httprequest.host_url if http_request else None
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
        try:
            response = requests.post(url, headers=headers, data=payload, timeout=REQUEST_TIMEOUT)
        except requests.exceptions.Timeout:
            raise UserError(_("The Muqeem service request timed out. Please try again."))
        except requests.exceptions.RequestException as err:
            raise UserError(_("Could not connect to Muqeem service: %s") % str(err))

        if response.status_code == 200:
            try:
                result = response.json()
            except ValueError:
                raise UserError(_("Muqeem service returned an invalid response format."))

            if result.get('error'):
                _logger.info('error result: %s', result)
                _logger.info('error: %s', result.get('error'))

                raise ValidationError(_('Invalid username or password, or connection timeout.'))

            elif result.get('result'):
                response = result.get('result')

                _logger.info('Success result: %s', result)
                _logger.info('Success response payload: %s', response)

                return response
            raise UserError(_("Muqeem service returned an unexpected response payload."))
        else:
            _logger.error('Failed to call muqeem service, status code: %s, response: %s', response.status_code,
                          response.text)
            if response.status_code == 500:
                raise ValidationError(_('Failed to call muqeem service'))

            if response.status_code == 403:
                raise ValidationError(_('Please check your subscription with Era Group info@era.net.sa'))

            return {
                'status': 'error',
                'message': 'Failed to call muqeem service'
            }

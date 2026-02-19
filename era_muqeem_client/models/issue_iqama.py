import json

import requests
from odoo import _, fields, models
from odoo.exceptions import ValidationError, UserError

REQUEST_TIMEOUT = 60


class IssueIqama(models.TransientModel):
    _name = "issue.iqama.wizard"

    employee_id = fields.Many2one('hr.employee', string="Resident")
    iqamaNumber = fields.Char(related="employee_id.iqamaNumber", string="Iqama Number", readonly=True, required=True)
    iqamaDuration = fields.Selection(
        [('3', '3'), ('6', '6'), ('9', '9'), ('12', '12'), ('15', '15'), ('18', '18'), ('21', '21'), ('24', '24')],
        string='IqamaDuration',
        required=True,
    )

    def _get_api_config(self):
        params = self.env['ir.config_parameter'].sudo()
        config = {
            'base_url': params.get_param('era_muqeem_client.url'),
            'username': params.get_param('era_muqeem_client.user_name'),
            'password': params.get_param('era_muqeem_client.user_pass'),
            'app_id': params.get_param('era_muqeem_client.user_app_id'),
            'app_key': params.get_param('era_muqeem_client.user_app_key'),
            # Backward compatibility with previous key naming.
            'x_integrator_id': params.get_param('era_muqeem_client.user_x_integrator_id')
            or params.get_param('era_muqeem_client.user_X_INTEGRATOR_ID'),
        }
        missing = [label for label, value in {
            'Base URL': config['base_url'],
            'User Name': config['username'],
            'User Password': config['password'],
            'App ID': config['app_id'],
            'App Key': config['app_key'],
            'X Integrator ID': config['x_integrator_id'],
        }.items() if not value]
        if missing:
            raise ValidationError(_('Configuration missing: %s') % ', '.join(missing))
        config['base_url'] = config['base_url'].rstrip('/')
        return config

    def get_token(self):
        config = self._get_api_config()
        url = "%s/api/authenticate" % config['base_url']

        payload = json.dumps({
            "username": config['username'],
            "password": config['password'],
        })
        headers = {
            'app-id': config['app_id'],
            'app-key': config['app_key'],
            'X-INTEGRATOR-ID': config['x_integrator_id'],
            'Content-Type': 'application/json',
        }

        try:
            response = requests.post(url, headers=headers, data=payload, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            token = response.json().get('id_token')
            if not token:
                raise ValidationError(_("Failed to retrieve token"))
            return token
        except requests.exceptions.Timeout:
            raise UserError(_("The request timed out. Please try again later."))
        except requests.exceptions.RequestException as e:
            raise UserError(_("An error occurred while connecting to the API: %s") % str(e))

    def renew_iqama(self):
        config = self._get_api_config()
        url = "%s/api/v1/iqama/issue" % config['base_url']

        headers = {
            'app-id': config['app_id'],
            'app-key': config['app_key'],
            'Authorization': f'Bearer {self.get_token()}',
            'X-INTEGRATOR-ID': config['x_integrator_id'],
            'Content-Type': 'application/json',
        }

        payload = {
            "iqamaNumber": self.iqamaNumber,
            "iqamaDuration": self.iqamaDuration,
        }
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=REQUEST_TIMEOUT)
            try:
                response_data = response.json()
            except ValueError:
                raise UserError(_("Invalid response payload returned by Muqeem API."))

            user_lang = self.env.user.lang
            if response_data.get("message", {}).get("en") == "Error in input data ":
                report_data = {
                    "ar": "خطأ في البيانات المدخلة",
                    "en": "Error in input data ",
                    'user_lang': user_lang,
                }
            else:
                self.employee_id.message_post(body=_('Issue Iqama'))
                report_data = {
                    'residentName': response_data.get('residentName'),
                    'translatedResidentName': response_data.get('translatedResidentName') or False,
                    'iqamaNumber': response_data.get('iqamaNumber'),
                    'versionNumber': response_data.get('versionNumber'),
                    'newIqamaExpiryDateHij': response_data.get('newIqamaExpiryDateHij'),
                    'newIqamaExpiryDateGre': response_data.get('newIqamaExpiryDateGre'),
                }

            data_return = {
                'form': self.read()[0],
                'data': [report_data],
            }

            return self.env.ref("era_muqeem_client.renew_iqama_report_id").report_action(self, data=data_return)
        except requests.exceptions.Timeout:
            raise UserError(_("The request timed out. Please try again later."))
        except requests.exceptions.RequestException as e:
            raise UserError(_("An error occurred while connecting to the API: %s") % str(e))

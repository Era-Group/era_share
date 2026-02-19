import json

import requests
from odoo import _, fields, models
from odoo.exceptions import ValidationError, UserError

REQUEST_TIMEOUT = 60


class ExtendPassValid(models.TransientModel):
    _name = "extend.passport.validaty"

    employee_id = fields.Many2one('hr.employee', string="Resident", readonly=True)
    iqamaNumber = fields.Char(related="employee_id.identification_id", string="Iqama Number", readonly=True, required=True)
    type_update = fields.Selection([('extend', 'Extend'), ('renew', 'Renew')], string="TypeUpdate", required=True)
    newPassportExpiryDate = fields.Date(string="NewPassportExpiry")
    newPassportIssueDate = fields.Date(string="NewPassportIssueDate")
    newPassportExpiryDate2 = fields.Date(string="newPassportExpiryDate")
    passportNumber = fields.Char(related="employee_id.passport_id", string='CurrentPassportNumber')
    newPassportNumber = fields.Char(string='NewPassportNumber')

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

    def extend_passport(self):
        if self.type_update != 'extend':
            raise UserError(_("Renew passport flow is not supported in this version yet."))

        if not self.newPassportExpiryDate:
            raise ValidationError(_('New passport expiry date is required.'))
        if not self.passportNumber:
            raise ValidationError(_('Current passport number is required.'))

        config = self._get_api_config()
        url = "%s/api/v1/update-information/extend" % config['base_url']
        headers = {
            'app-id': config['app_id'],
            'app-key': config['app_key'],
            'Authorization': f'Bearer {self.get_token()}',
            'X-INTEGRATOR-ID': config['x_integrator_id'],
            'Content-Type': 'application/json',
        }
        payload = {
            "iqamaNumber": self.iqamaNumber,
            "newPassportExpiryDate": self.newPassportExpiryDate.strftime('%Y-%m-%d'),
            "passportNumber": self.passportNumber,
        }

        try:
            response = requests.post(url, headers=headers, json=payload, timeout=REQUEST_TIMEOUT)
            try:
                response_data = response.json()
            except ValueError:
                raise UserError(_("Invalid response payload returned by Muqeem API."))
        except requests.exceptions.Timeout:
            raise UserError(_("The request timed out. Please try again later."))
        except requests.exceptions.RequestException as e:
            raise UserError(_("An error occurred while connecting to the API: %s") % str(e))

        user_lang = self.env.user.lang
        if response.status_code == 200:
            self.employee_id.expriry_pass_date = self.newPassportExpiryDate
            self.employee_id.message_post(body=_('Extend Passport'))
            report_data = {'message': 'The passport validity has been successfully extended'}
        elif response_data.get("message", {}).get("en") == "Error in input data ":
            report_data = {
                "ar": "خطأ في البيانات المدخلة",
                "en": "Error in input data ",
                'user_lang': user_lang,
            }
        else:
            message = response_data.get('message')
            if isinstance(message, dict):
                message = message.get('en') or message.get('ar')
            return self.env.company.show_popup(_('Error'), message or _('Failed to extend passport data.'))

        data_return = {
            'form': self.read()[0],
            'data': [report_data],
        }
        return self.env.ref("era_muqeem_client.extend_passport_report_id").report_action(self, data=data_return)

import json
from datetime import datetime

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


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
    json_data = fields.Char(string='JSON Data', compute='_compute_json_data')

    def convert_to_json(self):
        for record in self:
            data = {
                'iqamaNumber': record.iqamaNumber,
                'typeUpdate': record.type_update,
                'passportNumber': record.passportNumber,
                'newPassportExpiryDate': record.newPassportExpiryDate.strftime('%Y-%m-%d') if record.newPassportExpiryDate else False,
                'newPassportIssueDate': record.newPassportIssueDate.strftime('%Y-%m-%d') if record.newPassportIssueDate else False,
                'newPassportNumber': record.newPassportNumber,
            }
            return json.dumps(data)
        return json.dumps({})

    @api.depends('iqamaNumber', 'type_update', 'passportNumber', 'newPassportExpiryDate', 'newPassportIssueDate', 'newPassportNumber')
    def _compute_json_data(self):
        for record in self:
            record.json_data = record.convert_to_json()

    def extend_passport(self):
        if self.type_update != 'extend':
            raise ValidationError(_('Renew passport flow is not supported in this version yet.'))
        if not self.newPassportExpiryDate:
            raise ValidationError(_('New passport expiry date is required.'))
        if not self.passportNumber:
            raise ValidationError(_('Current passport number is required.'))

        company = self.env.company
        user_name, user_password = company._get_api_credentials_client()
        response_data = company.era_call_muqeem(json.loads(self.json_data), '12', user_name, user_password)

        vals = {
            'name': _('Extend Passport'),
            'user': self.env.user.name,
            'employee': self.employee_id.name,
            'date': datetime.now(),
        }
        record = self.env['client.requests'].create(vals)

        if isinstance(response_data, dict):
            status_code = response_data.get('statusCode')

            if status_code == 200:
                self.employee_id.expriry_pass_date = self.newPassportExpiryDate
                self.employee_id.message_post(body=_('Extend Passport'))
                record.update({'des': _('Success')})
                report_data = {
                    'message': _('The passport validity has been successfully extended'),
                }
                data_return = {
                    'form': self.read()[0],
                    'data': [report_data],
                }
                return self.env.ref("era_muqeem_client.extend_passport_report_id").report_action(self, data=data_return)

            if status_code in (500, 429, 401):
                record.update({'des': _('Fail')})
                return company.show_popup(_('Error'), response_data.get('message'))

            if status_code == 400:
                record.update({'des': _('Fail')})
                if response_data.get('fieldErrors'):
                    error_messages = []
                    for error in response_data.get('fieldErrors'):
                        field = error.get('field')
                        message = error.get('message')
                        error_messages.append(f"{field}: {message}")
                    return company.show_popup(_('Error'), '\n'.join(error_messages))
                return company.show_popup(_('Error'), response_data.get('message'))

        record.update({'des': _('Fail')})
        return company.show_popup(_('Error'), _('Unexpected response from Muqeem service'))

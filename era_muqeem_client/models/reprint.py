import json
import requests
from odoo import models, fields, api, _
from odoo.exceptions import AccessError, ValidationError, UserError
from base64 import b64decode
from markupsafe import Markup
from datetime import datetime




class ReprintVisa(models.TransientModel):
    _name = "reprint.visa.wizard"

    employee_id = fields.Many2one('hr.employee', string="Resident")
    iqamaNumber = fields.Char(related="employee_id.iqamaNumber", string="Iqama Number", readonly=True,required=True)
    visa_number = fields.Char( string="Visa Number", readonly=False,required=True)

    json_data = fields.Char(string='JSON Data', compute='_compute_json_data')

    def convert_to_json(self):
        for record in self:
            data = {
                'iqamaNumber': record.iqamaNumber,
                'visaNumber': record.visa_number,
            }

        json_data = json.dumps(data)
        return json_data

    @api.depends('iqamaNumber', 'visa_number')
    def _compute_json_data(self):
        for record in self:
            record.json_data = record.convert_to_json()

    def reprint_reentry_exit(self):

        json_data = self.json_data
        url_muqeem = '8'
        # Use the EMPLOYEE'S company
        company = self.employee_id.company_id or self.env.company
        credentials = company._get_api_credentials_client()
        user_name, user_password = credentials
        response_data = company.era_call_muqeem(json.loads(json_data), url_muqeem, user_name, user_password)
        user = self.env.user.name
        employee = self.employee_id.name
        process = _("RePrint Reentry Exit")
        current_datetime = datetime.now()
        vals = {
            'name': process,
            'user': user,
            'employee': employee,
            'date': current_datetime,

        }
        record = self.env['client.requests'].create(vals)


        statusCode=response_data.get('statusCode')
        if isinstance(response_data, dict):

            if statusCode == 200:

                name = _("Attachment Muqeem")
                era_visa = response_data.get('response_data').get('ervisaPDF')
                print("$$$$",era_visa)
                ervisaPDF = b64decode(era_visa, validate=True)
                if ervisaPDF[:4] != b'%PDF':
                    raise ValidationError(_('Missing the PDF file signature'))

                message = (_(' Reprint Exit Reentry Visa'))
                employee = self.employee_id
                employee.message_post(body=message, attachments=[(name, ervisaPDF)])
                record.update({'des': _('Success')})

                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _('Success'),
                        'message': _(' Reprint Exit Reentry has been successed'),
                        'type': 'success',
                        'sticky': True,
                        'next': {
                            'type': 'ir.actions.act_window_close'
                        }
                    }
                }

            elif statusCode == 500:
                record.update({'des': _('Fail')})

                return company.show_popup(_('Error'), response_data.get('message'))
            elif statusCode == 429:
                record.update({'des': _('Fail')})

                return company.show_popup(_('Error'), response_data.get('message'))

            elif statusCode == 401:
                record.update({'des': _('Fail')})

                return company.show_popup(_('Error'), response_data.get('message'))

            elif statusCode == 400:
                record.update({'des': _('Fail')})

                if response_data.get('fieldErrors'):

                    errors = response_data.get('fieldErrors')
                    print('errors', errors)
                    error_messages = []
                    for error in errors:
                        field = error.get('field')
                        message = error.get('message')
                        error_messages.append(f"{field}: {message}")
                    record.update({'des': _('Fail')})

                    return company.show_popup(_('Error'), '\n'.join(error_messages))
                else:
                    record.update({'des': _('Fail')})

                    return company.show_popup(_('Error'), response_data.get('message'))




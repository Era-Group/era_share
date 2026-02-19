import json
import base64
from base64 import b64decode
import requests
from odoo import models, fields, api, _
from odoo.exceptions import AccessError, ValidationError, UserError
from markupsafe import Markup
from datetime import datetime





class RenewIqama(models.TransientModel):
    _name = "renew.iqama.wizard"
    _description = "Renew Iqama Wizard"

    employee_id = fields.Many2one('hr.employee', string="Resident",readonly=True)
    iqamaNumber = fields.Char(related="employee_id.identification_id", string="Iqama Number", readonly=True,required=True)
    iqamaDuration = fields.Selection([('3', '3'), ('6', '6'), ('9', '9'), ('12', '12'), ('15', '15'), ('18', '18'), ('21', '21'), ('24', '24')], string='IqamaDuration',required=True)
    json_data = fields.Char(string='JSON Data', compute='_compute_json_data')

    def convert_to_json(self):
        for record in self:
            data = {
                'iqamaNumber': record.iqamaNumber,
                'iqamaDuration': record.iqamaDuration,
            }

        json_data = json.dumps(data)
        return json_data

    @api.depends('iqamaNumber','iqamaDuration')
    def _compute_json_data(self):
        for record in self:
            record.json_data = record.convert_to_json()


    def renew_iqama(self):
        company = self.env.company

        json_data = self.json_data
        url_muqeem = '7'
        user_name,user_password =  company._get_api_credentials_client()

        response_data = company.era_call_muqeem(json.loads(json_data), url_muqeem,user_name,user_password)
        user = self.env.user.name
        employee_name = self.employee_id.name
        process = _("Renew Iqama")
        current_datetime = datetime.now()
        vals = {
            'name': process,
            'user': user,
            'employee': employee_name,
            'date': current_datetime,

        }
        record = self.env['client.requests'].create(vals)

        lang=self.env.user.lang

        if isinstance(response_data, dict):

            if response_data.get('statusCode') == 200:
                datas_main = response_data.get('response_data')

                report_data = {
                    'residentName': datas_main.get('residentName'),
                    'translatedResidentName': datas_main.get('translatedResidentName',''),
                    'iqamaNumber': datas_main.get('iqamaNumber'),
                    'versionNumber': datas_main.get('versionNumber'),
                    'newIqamaExpiryDateHij': datas_main.get('newIqamaExpiryDateHij'),
                    'newIqamaExpiryDateGre': datas_main.get('newIqamaExpiryDateGre'),
                }

                new_iqama_date=report_data['newIqamaExpiryDateGre']

                mess1 = _('Resident Name: {}').format(report_data['residentName'])
                mess2 = _('TranslatedResidentName: {}').format(report_data['translatedResidentName'])
                mess3 = _('Iqama Number: {}').format(report_data['iqamaNumber'])
                mess4 = _('VersionNumber: {}').format(report_data['versionNumber'])
                mess5 = _('NewIqamaExpiryDateHij: {}').format(report_data['newIqamaExpiryDateHij'])
                mess6 = _('NewIqamaExpiryDateGre: {}').format(report_data['newIqamaExpiryDateGre'])

                message_body = (
                        _(' Renew Iqama') +
                        Markup('<br/>\n') +
                        mess1 + Markup('<br/>\n') +
                        mess2 + Markup('<br/>\n') +
                        mess3 + Markup('<br/>\n') +
                        mess4 + Markup('<br/>\n') +
                        mess5 + Markup('<br/>\n') +
                        mess6 + Markup('<br/>\n'))
                employee = self.employee_id
                employee.expriry_date_iqama=new_iqama_date
                employee.message_post(
                    body=message_body,

                )
                record.update({'des':_ ('Success')})


                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _('Success'),
                        'message': _('The Renew Iqama has been successed'),
                        'type': 'success',
                        'sticky': True,
                        'next': {
                            'type': 'ir.actions.act_window_close'
                        }
                    }
                }

            elif response_data.get('statusCode') == 500:
                record.update({'des': _('Fail')})

                return company.show_popup(_('Error'), response_data.get('message'))
            elif response_data.get('statusCode') == 429:
                record.update({'des': _('Fail')})

                return company.show_popup(_('Error'), response_data.get('message'))

            elif response_data.get('statusCode') == 401:
                record.update({'des': _('Fail')})

                return company.show_popup(_('Error'), response_data.get('message'))

            elif response_data.get('statusCode') == 400:
                record.update({'des': _('Fail')})

                if response_data.get('fieldErrors'):

                    errors = response_data.get('fieldErrors')
                    error_messages = []
                    for error in errors:
                        field = error.get('field')
                        message = error.get('message')
                        if field == 'iqamaDuration' and lang == 'ar_001':
                            field_ar = "حقل مدة التجديد"
                            message_ar = "مدة التجديد يجب ان يكون مساوى (3,6,9,12) شهور "
                            error_messages.append(_('%s: %s') % (field_ar, message_ar))
                        else:
                                error_messages.append(f"{field}: {message}")
                    record.update({'des': _('Fail')})

                    return company.show_popup(_('Error'), '\n'.join(error_messages))
                else:
                    record.update({'des': _('Fail')})

                    return company.show_popup(_('Error'), response_data.get('message'))

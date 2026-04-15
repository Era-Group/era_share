import json
import base64
from base64 import b64decode
import requests
from odoo import models, fields, api, _
from odoo.exceptions import AccessError, ValidationError, UserError
from markupsafe import Markup
from datetime import datetime




class TransferIqama(models.TransientModel):
    _name = "transfer.iqama.wizard"

    employee_id = fields.Many2one('hr.employee', string="Resident",readonly=True)
    iqamaNumber = fields.Char(related="employee_id.iqamaNumber", string="Iqama Number", readonly=True,required=True)
    newSponsorId = fields.Char( string='NewSponsor',required=True)

    json_data = fields.Char(string='JSON Data', compute='_compute_json_data')

    def convert_to_json(self):
        for record in self:
            data = {
                'iqamaNumber': record.iqamaNumber,
                'newSponsorId': record.newSponsorId,
            }

        json_data = json.dumps(data)
        return json_data

    @api.depends('iqamaNumber', 'newSponsorId')
    def _compute_json_data(self):
        for record in self:
            record.json_data = record.convert_to_json()


    def transfer_iqama(self):

        json_data = self.json_data
        url_muqeem = '10'
        # Use the EMPLOYEE'S company
        company = self.employee_id.company_id or self.env.company
        credentials = company._get_api_credentials_client()
        user_name, user_password = credentials
        response_data = company.era_call_muqeem(json.loads(json_data), url_muqeem, user_name, user_password)
        user = self.env.user.name
        employee_name = self.employee_id.name
        process = _("Transfer Iqama")
        current_datetime = datetime.now()
        vals = {
            'name': process,
            'user': user,
            'employee': employee_name,
            'date': current_datetime,

        }
        record = self.env['client.requests'].create(vals)


        print('&&&&response_data',response_data)
        statusCode=response_data.get('statusCode')
        lang=self.env.user.lang

        if isinstance(response_data, dict):

            if statusCode == 200:

                datas_main = response_data.get('response_data')


                report_data = {
                    'residentName': datas_main['residentName'],
                    'iqamaNumber': datas_main['iqamaNumber'],
                    'iqamaExpiryDateH': datas_main['iqamaExpiryDateH'],
                    'iqamaExpiryDateG': datas_main['iqamaExpiryDateG'],
                    'occupationcode': datas_main['occupation']['code'],
                    'occupationar': datas_main['occupation']['ar'],
                    'occupationen': datas_main['occupation']['en'],
                    'nationalitycode': datas_main['nationality']['code'],
                    'nationalityar': datas_main['nationality']['ar'],
                    'nationalityen': datas_main['nationality']['en'],
                    'religioncode': datas_main['religion']['code'],
                    'religionar': datas_main['religion']['ar'],
                    'religionen': datas_main['religion']['en'],
                    'gendercode': datas_main['gender']['code'],
                    'genderar': datas_main['gender']['ar'],
                    'genderen': datas_main['gender']['en'],
                    'passportNumber': datas_main['passportNumber'],

                }
                mess1 = _('Resident Name: {}').format(report_data['residentName'])
                mess2 = _('Iqama Number: {}').format(report_data['iqamaNumber'])
                mess3 = _('IqamaExpiryDateH: {}').format(report_data['iqamaExpiryDateH'])
                mess4 = _('IqamaExpiryDateG: {}').format(report_data['iqamaExpiryDateG'])
                mess5 = _('Occupationcode: {}').format(report_data['occupationcode'])
                mess6 = _('Occupationar: {}').format(report_data['occupationar'])
                mess7 = _('Occupationen: {}').format(report_data['occupationen'])
                mess8 = _('Nationalitycode: {}').format(report_data['nationalitycode'])
                mess9 = _('Nationalityar: {}').format(report_data['nationalityar'])
                mess10 = _('Nationalityen: {}').format(report_data['nationalityen'])
                mess11 = _('Religioncode: {}').format(report_data['religioncode'])
                mess12 = _('Religionar: {}').format(report_data['religionar'])
                mess13 = _('Religionen: {}').format(report_data['religionen'])
                mess14 = _('Gendercode: {}').format(report_data['gendercode'])
                mess15 = _('Genderar: {}').format(report_data['genderar'])
                mess16 = _('Genderen: {}').format(report_data['genderen'])
                mess17 = _('PassportNumber: {}').format(report_data['passportNumber'])

                message_body = (
                        _(' Transfer Iqama') +
                        Markup('<br/>\n') +
                        mess1 + Markup('<br/>\n') +
                        mess2 + Markup('<br/>\n') +
                        mess3 + Markup('<br/>\n') +
                        mess4 + Markup('<br/>\n') +
                        mess5 + Markup('<br/>\n') +
                        mess6 + Markup('<br/>\n') +
                        mess7 + Markup('<br/>\n') +
                        mess8 + Markup('<br/>\n') +
                        mess9 + Markup('<br/>\n') +
                        mess10 + Markup('<br/>\n') +
                        mess11 + Markup('<br/>\n') +
                        mess12 + Markup('<br/>\n') +
                        mess13 + Markup('<br/>\n') +
                        mess14 + Markup('<br/>\n') +
                        mess15 + Markup('<br/>\n') +
                        mess16 + Markup('<br/>\n') +
                        mess17 + Markup('<br/>\n'))
                employee=self.employee_id
                employee.message_post(
                    body=message_body,

                )
                record.update({'des': _('Success')})


                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _('Success'),
                        'message': _('Transfer Iqama has been successed'),
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
                    print('errors',errors)
                    error_messages = []
                    for error in errors:
                        field = error.get('field')
                        message = error.get('message')
                        error_messages.append(f"{field}: {message}")
                    record.update({'des': 'Fail'})

                    return company.show_popup(_('Error'), '\n'.join(error_messages))
                else:
                    messageen= response_data.get('message')
                    if lang == 'en_US':
                        record.update({'des': _('Fail')})

                        return company.show_popup(_('Error'), messageen)
                    else:

                        record.update({'des': _('Fail')})

                        message_ar = "رقم صاحب العمل الذي تم إدخاله ليس ضمن المؤسسة"
                        return company.show_popup(_('Error'), message_ar)





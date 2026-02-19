import json
import requests
from odoo import models, fields, api, _
from odoo.exceptions import AccessError, ValidationError, UserError
import os
from markupsafe import Markup, escape
from datetime import datetime


from base64 import b64decode


class CancelFinalExit(models.TransientModel):
    _name = "cancel.final.exit.wizard"
    _description = "Cancel Final Exit Wizard"

    employee_id = fields.Many2one('hr.employee', string="Resident",readonly=True)
    iqamaNumber = fields.Char(related="employee_id.identification_id", string="Iqama Number", readonly=True,required=True)
    feVisaNumber = fields.Char( string="Visa Number", required=True)
    # num_visa=fields.Char(string='Number Visa')
    confirm = fields.Boolean(string="Confirm", default=False,required=True)

    json_data = fields.Char(string='JSON Data', compute='_compute_json_data')

    def convert_to_json(self):
        for record in self:
            data = {
                'iqamaNumber': record.iqamaNumber,
                'feVisaNumber': record.feVisaNumber,
            }

        json_data = json.dumps(data)
        return json_data

    @api.depends('iqamaNumber', 'feVisaNumber')
    def _compute_json_data(self):
        for record in self:
            record.json_data = record.convert_to_json()



    def action_confirm_print(self):

        self.ensure_one()
        context = dict(self.env.context or {})
        context.update({
            'default_employee_id': self.employee_id.id,
            'default_iqamaNumber': self.iqamaNumber,
            'default_feVisaNumber': self.feVisaNumber,
            'default_confirm': self.confirm,
        })
        return {
            'name': 'سوف يتم ارسال البيانات الى وزارة الداخلية بناءا على مسؤليتكم المباشرة',
            'type': 'ir.actions.act_window',
            'view_mode': 'form',
            'res_model': 'cancel.final.exit.wizard',
            'view_id': self.env.ref('era_muqeem_client.view_cancell_print_confirmation').id,
            'target': 'new',
            'context': context,
        }





    def cancel_final_exit(self):
        company = self.env.company
        credentials = company._get_api_credentials_client()
        if not credentials:
            raise ValidationError(_('Missing Username,Password'))

        user_name,user_password = credentials

        if self.confirm == False:
            raise ValidationError(_("You cant proceed in process until you check"))
        json_data = self.json_data
        url_muqeem = '2'
        company = self.env.company

        response_data = company.era_call_muqeem(json.loads(json_data), url_muqeem, user_name,user_password)
        user = self.env.user.name
        employee_name = self.employee_id.name
        process = _("Cancel final Exit")
        current_datetime = datetime.now()
        vals = {
            'name': process,
            'user': user,
            'employee': employee_name,
            'date': current_datetime,

        }
        record = self.env['client.requests'].create(vals)

        statusCode=response_data.get('statusCode')

        if isinstance(response_data, dict):

            if statusCode == 200:
                datas_main = response_data.get('response_data')

                main_resident = datas_main['mainResident']
                final_exit_visa = main_resident['finalExitVisa']
                fe_visa_cancellation = final_exit_visa['feVisaCancellation']

                name = (_("Attachment Muqeem"))
                employee = self.employee_id

                report_data = {
                    'residentName1': main_resident['residentName'],
                    'iqamaNumber1': main_resident['iqamaNumber'],
                    'passportNumber1': main_resident['passportNumber'],
                    'nationality1': main_resident['nationality'],
                    'occupation1': main_resident['occupation'],
                    'visaNumber2': final_exit_visa['visaNumber'],
                    'visaType2': final_exit_visa['visaType'],
                    'issuanceDateG2': final_exit_visa['issuanceDateG'],
                    'issuanceDateH2': final_exit_visa['issuanceDateH'],
                    'exitBeforeG2': final_exit_visa['exitBeforeG'],
                    'exitBeforeH2': final_exit_visa['exitBeforeH'],
                    'residentName3': fe_visa_cancellation['residentName'],
                    'iqamaNumber3': fe_visa_cancellation['iqamaNumber'],
                    'visaNumber3': fe_visa_cancellation['visaNumber'],
                    'visaStatus3': fe_visa_cancellation['visaStatus'],
                }
                mess1 = _('Resident Name: {}').format(report_data['residentName1'])
                mess2 = _('Iqama Number: {}').format(report_data['iqamaNumber1'])
                mess3 = _('Passport Number: {}').format(report_data['passportNumber1'])
                mess4 = _('Nationality: {}').format(report_data['nationality1'])
                mess5 = _('Occupation: {}').format(report_data['occupation1'])
                mess6 = _('Visa Type: {}').format(report_data['visaType2'])
                mess7 = _('Issuance Date (G): {}').format(report_data['issuanceDateG2'])
                mess8 = _('Issuance Date (H): {}').format(report_data['issuanceDateH2'])
                mess9 = _('Exit Before (G): {}').format(report_data['exitBeforeG2'])
                mess10 = _('Exit Before (H): {}').format(report_data['exitBeforeH2'])
                mess11 = _('Visa Number: {}').format(report_data['visaNumber3'])
                mess12 = _('Visa Status: {}').format(report_data['visaStatus3'])
                mes_resident = _('### Main Resident Data')
                mes_final_visa = _('### Final Exit Visa Data')
                mes_FE_Visa = _('### FE Visa Cancellation Data')

                message_body = (
                        _('Cancell Final Exit') +
                        Markup('<br/>\n') +
                        mes_resident + Markup('<br/>\n') +
                        mess1 + Markup('<br/>\n') +
                        mess2 + Markup('<br/>\n') +
                        mess3 + Markup('<br/>\n') +
                        mess4 + Markup('<br/>\n') +
                        mess5 + Markup('<br/>\n') +
                        mes_final_visa + Markup('<br/>\n') +
                        mess6 + Markup('<br/>\n') +
                        mess7 + Markup('<br/>\n') +
                        mess8 + Markup('<br/>\n') +
                        mess9 + Markup('<br/>\n') +
                        mess10 + Markup('<br/>\n') +
                        mes_FE_Visa + Markup('<br/>\n') +
                        mess11 + Markup('<br/>\n') +
                        mess12 + Markup('<br/>\n')
                )

                employee = self.employee_id
                employee.message_post(
                    body=message_body,
                )
                record.update({'des': _('Success')})

                return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Success'),
                    'message': _('Final Exit Visa Cancellation has been successed'),
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


                if response_data.get('fieldErrors'):

                    errors = response_data.get('fieldErrors')
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






import json
import requests
from odoo import models, fields, api, _
from odoo.exceptions import AccessError, ValidationError, UserError
from markupsafe import Markup, escape
from datetime import datetime




class CancellVisa(models.TransientModel):
    _name = "cancell.visa.wizard"

    employee_id = fields.Many2one('hr.employee', string="Resident" ,readonly=True)
    iqamaNumber = fields.Char(related="employee_id.identification_id", string="Iqama Number", readonly=True,required=True)
    feVisaNumber = fields.Char( string="Visa Number", required=True)
    confirm = fields.Boolean(string="Confirm", default=False,required=True)

    json_data = fields.Char(string='JSON Data', compute='_compute_json_data')

    def convert_to_json(self):
        for record in self:
            data = {
                'iqamaNumber': record.iqamaNumber,
                'erVisaNumber': record.feVisaNumber,
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
            'res_model': 'cancell.visa.wizard',
            'view_id': self.env.ref('era_muqeem_client.view_cancell_print_confirmation1').id,
            'target': 'new',
            'context': context,
        }

    def cancell_reentry_exit(self):
        if self.confirm == False:
            raise ValidationError(_("You cant proceed in process until you press confirm"))

        json_data = self.json_data
        url_muqeem = '3'
        company = self.env.company
        credentials = company._get_api_credentials_client()
        user_name,user_password = credentials
        response_data = company.era_call_muqeem(json.loads(json_data), url_muqeem, user_name,user_password)
        user = self.env.user.name
        employee_name = self.employee_id.name
        process = _("Cancel Exit Reentry")
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

                employee = self.employee_id

                report_data = {
                    'residentName': datas_main['residentName'],
                    'iqamaNumber': datas_main['iqamaNumber'],
                    'visaNumber': datas_main['visaNumber'],
                    'visaStatus': datas_main['visaStatus'],
                }
                mess1 = _('Resident Name: {}').format(report_data['residentName'])
                mess2 = _('Iqama Number: {}').format(report_data['iqamaNumber'])
                mess3 = _('Visa Number: {}').format(report_data['visaNumber'])
                mess4 = _('Visa Status: {}').format(report_data['visaStatus'])

                message_body = (
                        _('Cancel Exit Reentry Visa') +
                        Markup('<br/>\n') +
                        mess1 + Markup('<br/>\n') +
                        mess2 + Markup('<br/>\n') +
                        mess3 + Markup('<br/>\n') +
                        mess4 + Markup('<br/>\n')
                )

                employee.message_post(body=message_body)
                record.update({'des': _('Success')})



                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _('Success'),
                        'message': _('The exit visa cancellation has been successed'),
                        'type': 'success',
                        'sticky': True,
                        'next': {
                            'type': 'ir.actions.act_window_close'
                        }
                    }
                }

            elif statusCode== 500:
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







import json
import requests
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError, UserError
from hijri_converter import Gregorian
from markupsafe import Markup, escape
import logging
from datetime import datetime


_logger = logging.getLogger(__name__)





class ExtendExitEntry(models.TransientModel):
    _name = "extend.exit.entry.wizard"

    employee_id = fields.Many2one('hr.employee', string="Resident", readonly=True)
    iqamaNumber = fields.Char(related="employee_id.identification_id", string="Iqama Number", readonly=True, required=True)
    visaNumber = fields.Char( string="Visa Number",  required=True)
    visa_duration = fields.Char(string="Number of days")
    validity_visa = fields.Selection([('1', 'Number of days'), ('2', 'Return before date')], string='Validity Visa', required=True)
    date_field = fields.Date(string='Gregorian Date')
    returnBefore = fields.Date(string='ReturnBefore')
    hijri_date_field = fields.Char(string='Hijri Date', compute='_compute_hijri_date')

    json_data = fields.Char(string='JSON Data', compute='_compute_json_data')

    def convert_to_json(self):
        for record in self:
            if record.visa_duration:
                data = {
                    'iqamaNumber': record.iqamaNumber,
                    'visaNumber': record.visaNumber,
                    'visaDuration': record.visa_duration,
                }
            else:
                data = {
                    'iqamaNumber': record.iqamaNumber,
                    'visaNumber': record.visaNumber,
                    'returnBefore': record.hijri_date_field,
                }


        json_data = json.dumps(data)
        return json_data

    @api.depends('iqamaNumber', 'visaNumber','visa_duration','date_field')
    def _compute_json_data(self):
        for record in self:
            record.json_data = record.convert_to_json()

    @api.constrains('visa_duration')
    def _check_visa_duration(self):
        for record in self:
            if record.visa_duration and int(record.visa_duration) < 7:
                raise ValidationError(_("Number of days should be greater than 7"))

    @api.depends('returnBefore')
    def _compute_hijri_date(self):
        for record in self:
            if record.returnBefore:
                gregorian_date = Gregorian.fromisoformat(str(record.returnBefore))
                hijri_date = gregorian_date.to_hijri()
                record.hijri_date_field = f"{hijri_date.year:04d}-{hijri_date.month:02d}-{hijri_date.day:02d}"
            else:
                record.hijri_date_field = ''


    def extend_exit_entry(self):

        """Function to extend exit entry visa."""


        json_data = self.json_data
        url_muqeem = '4'
        company = self.env.company
        credentials = company._get_api_credentials_client()
        user_name,user_password = credentials
        response_data = company.era_call_muqeem(json.loads(json_data), url_muqeem ,user_name,user_password)

        user = self.env.user.name
        employee_name = self.employee_id.name
        process = _("Extend Exit Entry")
        current_datetime = datetime.now()
        vals = {
            'name': process,
            'user': user,
            'employee': employee_name,
            'date': current_datetime,

        }
        record = self.env['client.requests'].create(vals)

        statusCode=response_data.get('statusCode')
        lang=self.env.user.lang

        if isinstance(response_data, dict):

            if statusCode == 200:
                        datas_main = response_data.get('response_data')

                        report_data = {
                            'residentName': datas_main.get('residentName'),
                            'iqamaNumber': datas_main.get('iqamaNumber'),
                            'visaTypecode': datas_main['visaType']['code'],
                            'visaTypear': datas_main['visaType']['ar'],
                            'visaTypeen': datas_main['visaType']['en'],
                            'visaNumber': datas_main['visaNumber'],
                            'visaDurationBeforeExtension': datas_main['visaDurationBeforeExtension'],
                            'requestedExtendedDuration': datas_main['requestedExtendedDuration'],
                            'returnBeforeBeforeExtensionH': datas_main['returnBeforeBeforeExtensionH'],
                            'returnBeforeBeforeExtensionG': datas_main['returnBeforeBeforeExtensionG'],
                            'returnBeforeAfterExtensionH': datas_main['returnBeforeAfterExtensionH'],
                            'returnBeforeAfterExtensionG': datas_main['returnBeforeAfterExtensionG'],
                            'iqamaExpiryDateH': datas_main['iqamaExpiryDateH'],
                            'iqamaExpiryDateG': datas_main['iqamaExpiryDateG'],
                            'passportNumber': datas_main['passportNumber'],
                            'passportExpiryDateH': datas_main['passportExpiryDateH'],
                            'passportExpiryDateG': datas_main['passportExpiryDateG'],
                            'serviceCost': datas_main['serviceCost'],
                        }
                        mess1 = _('Resident Name: {}').format(report_data['residentName'])
                        mess2 = _('Iqama Number: {}').format(report_data['iqamaNumber'])
                        mess3 = _('VisaTypecode: {}').format(report_data['visaTypecode'])
                        mess4 = _('VisaTypear: {}').format(report_data['visaTypear'])
                        mess5 = _('VisaTypeen: {}').format(report_data['visaTypeen'])
                        mess6 = _('Visa Number: {}').format(report_data['visaNumber'])
                        mess7 = _('VisaDurationBeforeExtension: {}').format(report_data['visaDurationBeforeExtension'])
                        mess8 = _('RequestedExtendedDuration: {}').format(report_data['requestedExtendedDuration'])
                        mess9 = _('ReturnBeforeBeforeExtensionH: {}').format(report_data['returnBeforeBeforeExtensionH'])
                        mess10 = _('ReturnBeforeBeforeExtensionG: {}').format(report_data['returnBeforeBeforeExtensionG'])
                        mess11 = _('ReturnBeforeAfterExtensionH: {}').format(report_data['returnBeforeAfterExtensionH'])
                        mess12 = _('ReturnBeforeAfterExtensionG: {}').format(report_data['returnBeforeAfterExtensionG'])
                        mess13 = _('IqamaExpiryDateH: {}').format(report_data['iqamaExpiryDateH'])
                        mess14 = _('IqamaExpiryDateG: {}').format(report_data['iqamaExpiryDateG'])
                        mess15 = _('PassportNumber: {}').format(report_data['passportNumber'])
                        mess16 = _('PassportExpiryDateH: {}').format(report_data['passportExpiryDateH'])
                        mess17 = _('PassportExpiryDateG: {}').format(report_data['passportExpiryDateG'])
                        mess18 = _('ServiceCost: {}').format(report_data['serviceCost'])

                        message_body = (
                                _(' Extend Exit Reentry Visa') +
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
                                mess17 + Markup('<br/>\n') +
                                mess18 + Markup('<br/>\n')

                        )
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
                            'message': _('The Extend Exit Reentry visa has been successed'),
                            'type': 'success',
                            'sticky': True,
                            'next': {
                                'type': 'ir.actions.act_window_close'
                            }
                        }
                    }

            # elif response_data.get('statusCode') == 500:
            #     return company.show_popup(_('Error'), response_data.get('message'))
            elif statusCode == 429:
                record.update({'des': _('Fail')})

                return company.show_popup(_('Error'), response_data.get('message'))

            elif statusCode == 401:
                record.update({'des': _('Fail')})

                return company.show_popup(_('Error'), response_data.get('message'))

            elif statusCode== 400:
                record.update({'des': _('Fail')})

                if response_data.get('fieldErrors'):

                    errors = response_data.get('fieldErrors')
                    error_messages = []
                    for error in errors:
                        field = error.get('field')
                        message = error.get('message')
                        if field == 'returnBefore' and lang =='ar_001':
                            field_ar ="حقل العودة قبل تاريخ"
                            message_ar ="العودة قبل تاريخ يجب ان يكون فى المستقبل "
                            error_messages.append(_('%s: %s') % (field_ar, message_ar))

                        else:

                            error_messages.append(f"{field}: {message}")
                    record.update({'des': _('Fail')})

                    return company.show_popup(_('Error'), '\n'.join(error_messages))

                else:
                    if response_data.get('message') =='There is no ER Visa to extend' and lang =='en_US':

                        record.update({'des': _('Fail')})

                        return company.show_popup(_('Error'), response_data.get('message'))
                    else:
                        message_ar="رقم التاشيرة المطلوب تمديدها خطا"
                        record.update({'des': 'Fail'})

                        return company.show_popup(_('Error'), message_ar)


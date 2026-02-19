import json
import base64
from base64 import b64decode
import requests
from odoo import models, fields, api, _
from hijridate import Gregorian
from odoo.exceptions import AccessError, ValidationError, UserError
from markupsafe import Markup
import logging
from datetime import datetime



_logger = logging.getLogger(__name__)


def _to_hijri_string(date_value):
    hijri_date = Gregorian(date_value.year, date_value.month, date_value.day).to_hijri()
    return f"{hijri_date.year:04d}-{hijri_date.month:02d}-{hijri_date.day:02d}"





class AccessIqama(models.TransientModel):
    _name = "api.access.iqama.wizard"
    _description = "Issue Exit Reentry Wizard"

    employee_id = fields.Many2one('hr.employee', string="Resident", readonly=True)
    iqamaNumber = fields.Char(related="employee_id.identification_id", string="Iqama Number", readonly=True, required=True)
    visa_duration = fields.Char(string="Number of days")
    visa_type = fields.Selection([('1', 'single'), ('2', 'multiple')], string='Visa Type', required=True)
    validity_visa = fields.Selection([('1', 'Number of days'), ('2', 'Return before date')], string='Validity Visa', required=True)
    date_field = fields.Date(string='Gregorian Date')
    hijri_date_field = fields.Char(string='Hijri Date', compute='_compute_hijri_date')
    json_data = fields.Char(string='JSON Data', compute='_compute_json_data')

    def convert_to_json(self):
        for record in self:
            if record.visa_duration:
             data = {
                'iqamaNumber': record.iqamaNumber,
                'visaDuration': record.visa_duration,
                'visaType': int(record.visa_type),
             }
            else:
                data = {
                    'iqamaNumber': record.iqamaNumber,
                    'visaType': int(record.visa_type),
                    'returnBefore': record.hijri_date_field,
                }

            json_data = json.dumps(data)
            return json_data

    @api.depends( 'iqamaNumber', 'visa_duration', 'visa_type',  'date_field')
    def _compute_json_data(self):
        for record in self:
            record.json_data = record.convert_to_json()

    @api.constrains('visa_duration')
    def _check_visa_duration(self):
        for record in self:
            if  record.visa_duration:
                 if int(record.visa_duration) < 7:

                            raise ValidationError(_("Number of days should be greater than 7"))

    @api.depends('date_field')
    def _compute_hijri_date(self):
        for record in self:
            if record.date_field:
                record.hijri_date_field = _to_hijri_string(record.date_field)
            else:
                record.hijri_date_field = ''


    def exit_reentry_issue(self):

        json_data = self.json_data
        url_muqeem = '1'
        company = self.env.company
        credentials = company._get_api_credentials_client()
        user_name,user_password = credentials
        response_data = company.era_call_muqeem(json.loads(json_data), url_muqeem,user_name,user_password)
        user = self.env.user.name
        employee_name = self.employee_id.name
        process = _("Issue Exit Reentry")
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
                visa_pdf = response_data.get('response_data').get('ervisaPDF')

                name = _("Attachment Muqeem")
                era_visa= visa_pdf
                ervisaPDF = b64decode(era_visa, validate=True)
                if ervisaPDF[:4] != b'%PDF':
                    raise ValidationError(_('Missing the PDF file signature'))

                message = _('Exit Reenty Visa')
                employee = self.employee_id
                employee.message_post(body=message, attachments=[(name, ervisaPDF)])
                record.update({'des': _('Success')})

                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _('Success'),
                        'message': _('Exit Reenty Visa Issued Successfully'),
                        'type': 'success',
                        'sticky': True,
                        'next': {
                            'type': 'ir.actions.act_window_close'
                        }
                    }
                }

            if statusCode == 500:
                record.update({'des': _('Fail')})

                return company.show_popup(_('Error'), response_data.get('message'))
            if statusCode == 429:
                record.update({'des': _('Fail')})

                return company.show_popup(_('Error'), response_data.get('message'))

            elif statusCode == 401:
                record.update({'des': _('Fail')})

                return company.show_popup(_('Error'), response_data.get('message'))

            if statusCode == 400:
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
                            error_messages.append(_('%s: %s') % (field, message))
                    record.update({'des': _('Fail')})

                    return company.show_popup(_('Error'), '\n'.join(error_messages))
                else:
                    record.update({'des': _('Fail')})

                    return company.show_popup(_('Error'), response_data.get('message'))



import json
import base64
from base64 import b64decode
import requests
from odoo import models, fields, api, _
from odoo.exceptions import AccessError, ValidationError, UserError
from datetime import datetime


import logging
_logger = logging.getLogger(__name__)


class PrintMuqeemReport(models.TransientModel):
    _name = "print.muqeem.report"


    iqamaNumber = fields.Char(related="employee_id.iqamaNumber", string="Iqama Number", readonly=True,required=True)

    employee_id = fields.Many2one('hr.employee', string="Resident",readonly=True)
    language = fields.Selection([('ar','Arabic'),('en','English')],default="ar",string="Language",required=True)
    print = fields.Boolean( string='Print',default=True)

    json_data = fields.Char(string='JSON Data', compute='_compute_json_data')

    def convert_to_json(self):
        for record in self:
            data = {
                'iqamaNumber': record.iqamaNumber,
                'language': record.language,
                'print': record.print,
            }

        json_data = json.dumps(data)
        return json_data

    @api.depends('iqamaNumber', 'language','print')
    def _compute_json_data(self):
        for record in self:
            record.json_data = record.convert_to_json()





    def print_muqeem_report(self):

            json_data = self.json_data
            url_muqeem = '6'
            # Use the EMPLOYEE'S company
            company = self.employee_id.company_id or self.env.company
            credentials = company._get_api_credentials_client()
            user_name, user_password = credentials
            response_data = company.era_call_muqeem(json.loads(json_data), url_muqeem, user_name, user_password)
            # print('responsecon',response_data)
            user =self.env.user.name
            employee_name = self.employee_id.name
            process=_("Print Muqeem Report")
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
                # print('inside isinstance')
                statusCode = response_data.get('statusCode')
                if statusCode == 200:

                    content = response_data.get('content')

                    if isinstance(content, str):
                        content = content.encode('latin1')

                    if content[:4] != b'%PDF':
                        raise ValidationError(_('Missing the PDF file signature'))

                    if content:
                        if content[:4] == b'%PDF':
                            print('PDF signature is valid')

                        attachment_name = _("Attachment Print Muqeem.pdf")
                        employee = self.employee_id

                        if employee:
                            message = _('Print Muqeem')
                            try:
                                employee.message_post(
                                    body=message,
                                    attachments=[(attachment_name, content)]
                                )
                                record.update({'des': _('Success')})

                                return {
                                    'type': 'ir.actions.client',
                                    'tag': 'display_notification',
                                    'params': {
                                        'title': _('Success'),
                                        'message': _(
                                            'Print Muqeem Report has been successed'),
                                        'type': 'success',
                                        'sticky': True,
                                        'next': {
                                            'type': 'ir.actions.act_window_close'
                                        }
                                    }}

                                _logger.info("Attachment created successfully")
                            except Exception as e:
                                _logger.error("Failed to create attachment: %s", str(e))
                elif statusCode == 500:
                    record.update({'des': _('Fail')})

                    return company.show_popup(_('Error'), response_data.get('message'))
                elif statusCode == 429:
                    record.updaterecord.update({'des': _('Fail')})

                    return company.show_popup(_('Error'), response_data.get('message'))
                elif statusCode == 401:
                    record.update({'des': _('Fail')})

                    return company.show_popup(_('Error'), response_data.get('message'))
                elif statusCode == 400:
                    record.update({'des': _('Fail')})

                    # print('400')

                    if response_data.get('fieldErrors'):

                        errors = response_data.get('fieldErrors')
                        # print('errors', errors)
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






import json

from odoo import models, fields, api, _
from hijridate import Gregorian
import pytz
from odoo.tools.misc import format_date
from datetime import datetime




class TodayrequestReport(models.TransientModel):
    _name = "today.request.report.wizard"
    _description = "Today Requests Report Wizard"

    employee_id = fields.Many2one('hr.employee', string="Resident",readonly=True)
    date_from= fields.Date(string='Date From')
    date_to = fields.Date(string='Date To')
    fromDate = fields.Char(string='Datestr From',compute="_compute_str_date")
    toDate = fields.Char(string='Datestr To',compute="_compute_str_date")
    user_id= fields.Many2one('hr.employee', string="User")
    user_name = fields.Char(string="User Name", compute="_compute_user_name")
    name = fields.Char(string="Name", compute='_compute_report_name', store=True)

    json_data = fields.Char(string='JSON Data', compute='_compute_json_data')

    def convert_to_json(self):
        for record in self:
            data = {
                'fromDate': record.fromDate,
                'toDate': record.toDate,
                 "user": 'indirect1'

            }

        json_data = json.dumps(data)
        return json_data

    @api.depends('date_from', 'date_to')
    def _compute_json_data(self):
        for record in self:
            record.json_data = record.convert_to_json()


    @api.depends('date_from', 'date_to')
    def _compute_report_name(self):
        for rec in self:
            if rec.date_from and  rec.date_to:
                DateTime_from = datetime(rec.date_from.year, rec.date_from.month, rec.date_from.day)
                lang =  self.env.user.lang
                tz = self.env.user.tz or 'UTC'
                local_tz = pytz.timezone(tz)
                rec.name = "Requests Report {} - {}".format(rec.user_id.name, format_date(self.env,
                                                                                             DateTime_from.astimezone(
                                                                                                 local_tz).replace(
                                                                                                 tzinfo=None),
                                                                                             date_format="MMMM y",
                                                                                             lang_code=lang))



    @api.depends('date_from','date_to')
    def _compute_str_date(self):
        for record in self:
            if record.date_from and record.date_to:
                from_hijri = Gregorian(record.date_from.year, record.date_from.month, record.date_from.day).to_hijri()
                record.fromDate = f"{from_hijri.year:04d}-{from_hijri.month:02d}-{from_hijri.day:02d}"
                to_hijri = Gregorian(record.date_to.year, record.date_to.month, record.date_to.day).to_hijri()
                record.toDate = f"{to_hijri.year:04d}-{to_hijri.month:02d}-{to_hijri.day:02d}"
            else:
                record.fromDate = ''
                record.toDate = ''
    @api.depends('user_id')
    def _compute_user_name(self):
        for record in self:
            if record.user_id:
                record.user_name = record.user_id.name
            else:
                record.user_name = ''


    def request_report(self):
        list_dict=[]


        json_data = self.json_data
        url_muqeem = '9'
        company = self.env.company
        credentials = company._get_api_credentials_client()
        user_name,user_password = credentials
        response_data = company.era_call_muqeem(json.loads(json_data), url_muqeem,user_name,user_password)
        user = self.env.user.name
        employee_name = self.employee_id.name
        process = _("Today Request Report")
        current_datetime = datetime.now()
        vals = {
            'name': process,
            'user': user,
            'employee': employee_name,
            'date': current_datetime,

        }
        record = self.env['client.requests'].create(vals)

        lang=self.env.user.lang
        statusCode=response_data.get('statusCode')
        if isinstance(response_data, dict):

            if statusCode == 200:

                datas_main = response_data.get('response_data')
                for dict1 in datas_main:
                    report_data = {
                        'requestNumber': dict1['requestNumber'],
                        'company': dict1['company'],
                        'user': dict1['user'],
                        'iqamaNumber': dict1['iqamaNumber'],
                        'type': dict1['type'],
                        'description': dict1['description'],
                        'errorMessage': dict1['errorMessage'],
                        'date': dict1['date'],
                    }

                    list_dict.append(report_data)
                data_return = {
                        'form': self.read()[0],
                        'data': list_dict,
                    }

                record.update({'des': _('Success')})


                return self.env.ref("era_muqeem_client.today_request_report_id").report_action(self,
                                                                                                   data=data_return)
            elif statusCode == 500:
                record.update({'des':_ ('Fail')})

                return company.show_popup(_('Error'), response_data.get('message'))
            elif statusCode == 429:
                record.update({'des':_ ('Fail')})

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
                        if field == 'toDate' and lang == 'ar_001':
                            fieldar=' حقل الى تاريخ'
                            messagear='الى تاريخ يجب ان لايكون فى المستقبل'
                            error_messages.append(f"{fieldar}: {messagear}")
                            record.update({'des': ('الى تاريخ يجب ان لايكون فى المستقبل')})
                        else:
                          error_messages.append(f"{field}: {message}")
                          record.update({'des': _('toDate must not be a future date')})


                    return company.show_popup(_('Error'), '\n'.join(error_messages))
                else:
                    record.update({'des': _('Fail')})

                    return company.show_popup(_('Error'), response_data.get('message'))

    # def request_report_xlsx(self):
    #     list_dict = []
    #
    #     company = self.env['res.company'].search([], limit=1)
    #
    #     json_data = self.json_data
    #     url_muqeem = '/api/v1/report/interactive-services-report?size=1000&sort=transactionDate,DESC'
    #     company = self.env.company
    #     credentials = company._get_api_credentials_client()
    #     environment,user_name,user_password = credentials
    #     response_data = company.era_call_muqeem(json.loads(json_data), url_muqeem, environment,user_name,user_password)
    #     user = self.env.user.name
    #     employee = self.employee_id.name
    #     process = "Print Muqeem Report"
    #     current_datetime = datetime.now()
    #     vals = {
    #         'name': process,
    #         'user': user,
    #         'employee': employee,
    #         'date': current_datetime,
    #
    #     }
    #     record = self.env['client.requests'].create(vals)
    #
    #
    #     statusCode=response_data.get('statusCode')
    #     if isinstance(response_data, dict):
    #
    #         if statusCode == 200:
    #
    #             datas_main = response_data.get('response_data')
    #             for dict1 in datas_main:
    #                 report_data = {
    #                     'requestNumber': dict1['requestNumber'],
    #                     'company': dict1['company'],
    #                     'user': dict1['user'],
    #                     'iqamaNumber': dict1['iqamaNumber'],
    #                     'type': dict1['type'],
    #                     'description': dict1['description'],
    #                     'errorMessage': dict1['errorMessage'],
    #                     'date': dict1['date'],
    #                 }
    #
    #                 list_dict.append(report_data)
    #             data_return = {
    #                     'form': self.read()[0],
    #                     'data': list_dict,
    #                 }
    #
    #             record.update({'des': 'Success'})
    #
    #
    #             return self.env.ref("era_muqeem_client.report_request_today_xlsx_action").report_action(self,
    #                                                                                                data=data_return)
    #         elif statusCode == 500:
    #             record.update({'des': 'Fail'})
    #
    #             return company.show_popup(_('Error'), response_data.get('message'))
    #         elif statusCode == 429:
    #             record.update({'des': 'Fail'})
    #
    #             return company.show_popup(_('Error'), response_data.get('message'))
    #         elif statusCode == 400:
    #             record.update({'des': 'Fail'})
    #
    #             if response_data.get('fieldErrors'):
    #
    #                 errors = response_data.get('fieldErrors')
    #                 print('errors', errors)
    #                 error_messages = []
    #                 for error in errors:
    #                     field = error.get('field')
    #                     message = error.get('message')
    #
    #                     error_messages.append(f"{field}: {message}")
    #                 record.update({'des': 'Fail'})
    #
    #                 return company.show_popup(_('Error'), '\n'.join(error_messages))
    #             else:
    #                 record.update({'des': 'Fail'})
    #
    #                 return company.show_popup(_('Error'), response_data.get('message'))

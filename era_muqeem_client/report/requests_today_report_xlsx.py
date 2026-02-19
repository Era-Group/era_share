import datetime
from datetime import datetime
import datetime
from datetime import datetime
from odoo.tools.misc import format_date
from datetime import datetime, timedelta
import pytz
from odoo import models
import calendar


class RequestTodayCardXlsx(models.AbstractModel):
    _name = 'report.era_muqeem_client.report_request_today_xlsx'
    _inherit = 'report.report_xlsx.abstract'

    def generate_xlsx_report(self, workbook, data, residents, ):
        bold = workbook.add_format({'bold': True})
        bold = workbook.add_format({'bold': True})
        sheet = workbook.add_worksheet("Residents")

        full_border = workbook.add_format(
            {'bold': True, 'valign': 'vcenter', 'align': 'center', 'border': 1, 'border_color': '#000000',
             'align': 'center',
             'bg_color': '#D9D9D9'})
        sheet.set_column('A:A', 20)
        sheet.set_column('B:B', 15)
        sheet.set_column('C:C', 15)
        sheet.set_column('D:D', 15)
        sheet.set_column('E:E', 33)
        sheet.set_column('F:F', 15)
        sheet.set_column('G:G', 15)
        sheet.set_column('H:H', 26)

        row_header = 3
        col_header = 3
        row_head=7
        col=0
        sheet.merge_range(row_header, col_header, row_header , col_header+2,data['form']['name'] , full_border)
        sheet.write(row_head + 1, col, 'RequestNumber', full_border)
        sheet.write(row_head + 1, col+1, 'Company', full_border)
        sheet.write(row_head + 1, col+2, 'user', full_border)
        sheet.write(row_head + 1, col+3, 'IqamaNumber', full_border)
        sheet.write(row_head + 1, col+4, 'Type', full_border)
        sheet.write(row_head + 1, col+5, 'Description', full_border)
        sheet.write(row_head + 1, col+6, 'ErrorMessage', full_border)
        sheet.write(row_head + 1, col+7, 'Date', full_border)


        row_body=8
        col_body=0
        for obj in data['data']:
            for key,value in obj.items():
                # sheet.write(row_body + 1, col, value, full_border)
                sheet.write(row_body + 1, col, obj.get('requestNumber', ''), full_border)
                sheet.write(row_body + 1, col+1, obj.get('company', ''), full_border)
                sheet.write(row_body + 1, col+2, obj.get('user', ''), full_border)
                sheet.write(row_body + 1, col+3, obj.get('iqamaNumber', ''), full_border)
                sheet.write(row_body + 1, col+4, obj.get('type', ''), full_border)
                sheet.write(row_body + 1, col+5, obj.get('description', ''), full_border)
                sheet.write(row_body + 1, col+6, obj.get('errorMessage', ''), full_border)
                sheet.write(row_body + 1, col+7, obj.get('date', ''), full_border)
            row_body+=1




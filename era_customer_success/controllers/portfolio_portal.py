import io
import secrets

import xlsxwriter

from odoo import fields, http, _
from odoo.http import request
from werkzeug.exceptions import Forbidden, NotFound


class CsPortfolioPortal(http.Controller):

    def _get_share(self, share_id, access_token=None):
        share = request.env['cs.portfolio.share'].sudo().browse(share_id).exists()
        if not share:
            raise NotFound()
        valid_token = bool(access_token and share.access_token and secrets.compare_digest(access_token, share.access_token))
        valid_user = not request.env.user._is_public() and request.env.user in share.portal_user_ids
        if not share.active or not share.sharing_approved or share.expires_on < fields.Date.today() or not (valid_token or valid_user):
            raise Forbidden()
        return share

    @http.route('/era/portfolio/<int:share_id>', type='http', auth='public', website=True, sitemap=False)
    def portfolio(self, share_id, access_token=None, **kwargs):
        share = self._get_share(share_id, access_token)
        share.sudo().write({'last_accessed_on': fields.Datetime.now(), 'access_count': share.access_count + 1})
        return request.render('era_customer_success.portal_shared_portfolio', {
            'share': share, 'lines': share.line_ids, 'access_token': access_token or '',
        })

    @http.route('/era/portfolio/<int:share_id>/xlsx', type='http', auth='public', sitemap=False)
    def portfolio_xlsx(self, share_id, access_token=None, **kwargs):
        share = self._get_share(share_id, access_token)
        if not share.allow_export:
            raise Forbidden()
        output = io.BytesIO()
        workbook = xlsxwriter.Workbook(output, {'in_memory': True})
        sheet = workbook.add_worksheet('Customer Portfolio')
        header = workbook.add_format({'bold': True, 'bg_color': '#714B67', 'font_color': '#FFFFFF', 'text_wrap': True, 'align': 'center'})
        percent = workbook.add_format({'num_format': '0.00%'}); date_fmt = workbook.add_format({'num_format': 'yyyy-mm-dd'})
        headers = [
            'ERA CSM / مسؤول نجاح العملاء', 'ERA CSM phone / هاتف المسؤول', 'ERA CSM email / بريد المسؤول',
            'Customer Name / اسم العميل', 'Date of Join / تاريخ الانضمام', 'Next invoice date / الفاتورة القادمة',
            'Recurring plan / الخطة المتكررة', 'Industry / القطاع', '# of Employees / عدد الموظفين',
            '# of Users / عدد المستخدمين', 'Active Users / المستخدمون النشطون', 'Stage / المرحلة',
            'Version / الإصدار', 'Adoption / التفاعل', 'Client Website / موقع العميل',
            'Active Implemented modules / الموديولات المطبقة', 'Potential Expansion / التوسع المحتمل',
            'Next Action / الإجراء التالي', 'Extra Notes / ملاحظات إضافية', 'Expansion Status / حالة التوسع',
        ]
        for col, label in enumerate(headers): sheet.write(0, col, label, header)
        for row, line in enumerate(share.line_ids, start=1):
            values = [line.era_csm, line.era_csm_phone, line.era_csm_email, line.customer_name, line.date_of_join,
                      line.next_invoice_date, line.recurring_plan, line.industry, line.number_of_employees,
                      line.number_of_users, line.active_users, line.stage, line.version, line.adoption / 100,
                      line.client_website, line.active_implemented_modules, line.potential_expansion,
                      line.next_action, line.extra_notes, line.expansion_status or '']
            for col, value in enumerate(values):
                fmt = percent if col == 13 else date_fmt if col in (4, 5) and value else None
                sheet.write(row, col, value or '', fmt)
        sheet.freeze_panes(1, 4); sheet.autofilter(0, 0, len(share.line_ids), len(headers) - 1)
        sheet.set_column(0, 2, 22); sheet.set_column(3, 3, 34); sheet.set_column(4, 14, 18); sheet.set_column(15, 18, 34); sheet.set_column(19, 19, 20)
        workbook.close(); output.seek(0)
        return request.make_response(output.read(), headers=[
            ('Content-Type', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
            ('Content-Disposition', http.content_disposition('ERA_Odoo_Customer_Portfolio.xlsx')),
        ])

import werkzeug
from odoo import http
from odoo.http import request
from odoo.addons.portal.controllers.portal import CustomerPortal, pager as portal_pager
class LegalCustomerPortal(CustomerPortal):
    def _prepare_home_portal_values(self,counters):
        values=super()._prepare_home_portal_values(counters)
        if 'legal_case_count' in counters:values['legal_case_count']=request.env['legal.case'].search_count(self._case_domain())
        return values
    def _case_domain(self):return [('company_id','in',request.env.user.company_ids.ids),('client_id.commercial_partner_id','=',request.env.user.partner_id.commercial_partner_id.id)]
    def _case_check_access(self,case_id,access_token=None):
        case=request.env['legal.case'].sudo().browse(case_id).exists()
        allowed=case and ((case.access_token and access_token==case.access_token) or (not request.env.user._is_public() and case.company_id in request.env.user.company_ids and case.client_id.commercial_partner_id==request.env.user.partner_id.commercial_partner_id))
        return case if allowed else False
    def _document_check_access(self,case_id,document_id,access_token=None):
        case=self._case_check_access(case_id,access_token)
        document=case and case.document_ids.filtered(lambda d:d.id==document_id and d.portal_published and d.state=='approved')
        return case, document[:1]
    @http.route(['/my/legal-cases'],type='http',auth='user',website=True)
    def portal_my_legal_cases(self,**kw):
        cases=request.env['legal.case'].search(self._case_domain())
        return request.render('era_law_firm.portal_my_legal_cases',{'cases':cases,'page_name':'legal_cases'})
    @http.route(['/my/legal-cases/<int:case_id>'],type='http',auth='public',website=True)
    def portal_my_legal_case(self,case_id,access_token=None,**kw):
        case=self._case_check_access(case_id,access_token)
        if not case:return request.not_found()
        return request.render('era_law_firm.portal_legal_case',{'case':case,'documents':case.document_ids.filtered(lambda d:d.portal_published)})
    @http.route(['/my/legal-cases/<int:case_id>/documents/<int:document_id>/download'],type='http',auth='public',website=True)
    def portal_legal_document_download(self,case_id,document_id,access_token=None,**kw):
        case,document=self._document_check_access(case_id,document_id,access_token)
        if not document or not document.attachment_id:return request.not_found()
        attachment=document.attachment_id
        return request.make_response(attachment.raw or b'',headers=[('Content-Type',attachment.mimetype or 'application/octet-stream'),('Content-Disposition',werkzeug.http.dump_options_header('attachment',{'filename':attachment.name or document.name}))])

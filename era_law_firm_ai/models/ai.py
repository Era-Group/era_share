import hashlib
import re
from odoo import fields, models, _
from odoo.exceptions import UserError

class ResCompany(models.Model):
    _inherit='res.company'; legal_ai_enabled=fields.Boolean()

class LegalAIRequest(models.Model):
    _name='legal.ai.request';_description='Governed Legal AI Request';_inherit=['mail.thread'];_check_company_auto=True
    company_id=fields.Many2one('res.company',required=True,default=lambda s:s.env.company,index=True);case_id=fields.Many2one('legal.case',check_company=True);document_id=fields.Many2one('legal.document',check_company=True);purpose=fields.Char(required=True);fields_sent=fields.Text(compute='_compute_fields_sent',store=True,readonly=True);input_payload=fields.Text();redacted_payload=fields.Text(readonly=True);payload_hash=fields.Char(readonly=True);sanitized_response=fields.Text(readonly=True);consent_user_id=fields.Many2one('res.users');consent_date=fields.Datetime();state=fields.Selection([('draft','Draft'),('approved','Approved'),('sent','Sent'),('done','Done'),('rejected','Rejected'),('cancelled','Cancelled')],default='draft',tracking=True)
    _ALLOWED_FIELDS = {'name', 'najiz_number', 'case_type', 'jurisdiction_id', 'court_id', 'circuit_id', 'city', 'claim_amount', 'outcome', 'document_text'}
    def _check_provider_policy(self):
        for r in self:
            r._assert_gate_open()
            if r.document_id and r.document_id.ai_classification=='blocked':raise UserError(_('This document is blocked from AI processing.'))
            if r.case_id and not self.env.user.can_access_legal_case(r.case_id):raise UserError(_('You cannot send a case you cannot access.'))
            requested={item.strip() for item in (r.fields_sent or '').split(',') if item.strip()}
            if not requested or not requested <= self._ALLOWED_FIELDS:raise UserError(_('The AI request contains fields that are not allowed by policy.'))

    @staticmethod
    def _redact(value):
        # Order matters: a Saudi mobile is ten digits too, so it has to be matched
        # before the national-ID rule or every phone comes out labelled as an ID.
        value=re.sub(r'(?<![\d+])(?:\+?966|00966|0)?5\d{8}(?!\d)','[REDACTED-PHONE]',value or '')
        value=re.sub(r'(?<!\d)[12]\d{9}(?!\d)','[REDACTED-ID]',value)
        value=re.sub(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}','[REDACTED-EMAIL]',value)
        value=re.sub(r'(?<!\d)SA\d{22}(?!\d)','[REDACTED-IBAN]',value,flags=re.I)
        return re.sub(r'(?<!\d)\d{7,15}(?!\d)','[REDACTED-NUMBER]',value)
    def _prepare_redacted_payload(self):self.ensure_one();return self._redact(self.input_payload or '')
    def action_approve(self):
        if not self.env.user.has_group('era_law_firm_ai.group_legal_ai_user'):raise UserError(_('You are not authorized to approve AI requests.'))
        self.write({'consent_user_id':self.env.user.id,'consent_date':fields.Datetime.now(),'state':'approved'})
    def action_send(self):
        for r in self:
            r._check_provider_policy();payload=r._prepare_redacted_payload();digest=hashlib.sha256(payload.encode()).hexdigest();r.write({'input_payload':False,'instructions_sent':r._redact(r.input_payload or ''),'redacted_payload':payload,'payload_hash':digest,'state':'sent'});r.env['legal.audit.log'].log(r,'ai_send',['purpose','fields_sent','payload_hash'])
            r._dispatch_to_provider(payload)
    def _store_sanitized_response(self,response):self.ensure_one();self.write({'sanitized_response':self._redact(response)[:100000],'state':'done'})
    def action_reject(self):self.write({'state':'rejected'})
    def action_cancel(self):self.write({'state':'cancelled'})

class LegalDocument(models.Model):
    _inherit='legal.document';ai_classification=fields.Selection([('public','Public'),('internal','Internal'),('confidential','Confidential'),('blocked','Blocked')],default='confidential',required=True)

class LegalDocumentTemplate(models.Model):
    _name='legal.document.template';_description='Legal Document Template';_check_company_auto=True
    name=fields.Char(required=True,translate=True);company_id=fields.Many2one('res.company',required=True,default=lambda s:s.env.company,index=True);body=fields.Html(required=True);allowed_fields=fields.Char(required=True);active=fields.Boolean(default=True)

class LegalDocumentGeneration(models.Model):
    _name='legal.document.generation';_description='AI Legal Document Generation';_check_company_auto=True
    name=fields.Char(required=True);company_id=fields.Many2one('res.company',required=True,default=lambda s:s.env.company,index=True);request_id=fields.Many2one('legal.ai.request',required=True,ondelete='restrict');template_id=fields.Many2one('legal.document.template',check_company=True);document_id=fields.Many2one('legal.document',check_company=True,readonly=True);state=fields.Selection([('draft','Draft'),('generated','Generated'),('reviewed','Reviewed')],default='draft')
    def action_create_document(self):
        self.ensure_one()
        if self.request_id.state!='done' or not self.request_id.case_id:raise UserError(_('A completed AI request linked to a case is required.'))
        attachment=self.env['ir.attachment'].create({'name':self.name+'.txt','raw':(self.request_id.sanitized_response or '').encode(),'mimetype':'text/plain'})
        document=self.env['legal.document'].create({'name':self.name,'case_id':self.request_id.case_id.id,'attachment_id':attachment.id,'state':'draft','ai_classification':'confidential'})
        self.write({'document_id':document.id,'state':'generated'});return document

class ResConfigSettings(models.TransientModel):
    """The AI kill switch lives on res.company; without this it is unreachable."""
    _inherit='res.config.settings'
    legal_ai_enabled=fields.Boolean(related='company_id.legal_ai_enabled',readonly=False)

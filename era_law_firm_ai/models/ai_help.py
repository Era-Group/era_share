"""Field help text for the AI governance layer, declared incrementally."""

from odoo import fields, models


class LegalAIRequest(models.Model):
    _inherit = 'legal.ai.request'

    case_id = fields.Many2one(help="The file this request relates to. You cannot send a case you would not be allowed to open.")
    document_id = fields.Many2one(help="The document being processed. Its classification is checked against the agent's ceiling, and a document classified as blocked never leaves.")
    purpose = fields.Char(help="Why this request is being made. Recorded so the use of AI on client material can be justified later.")
    fields_sent = fields.Text(help="Filled from what you ticked above. Kept as the record of exactly which data left the firm.")
    input_payload = fields.Text(help="Your own instructions to the agent, sent alongside the ticked data. Redacted like everything else, and discarded once dispatched.")
    redacted_payload = fields.Text(help="Exactly what was sent, after identity numbers, phone numbers and e-mail addresses were stripped. Kept as the record of what left the firm.")
    payload_hash = fields.Char(help="Hash of the redacted payload, so what was sent can be proven later.")
    consent_user_id = fields.Many2one(help="The named person who consented to this material being sent. Consent is per request and cannot be given in advance.")
    consent_date = fields.Datetime(help="When consent was given.")
    state = fields.Selection(help="A request has to be consented to before it can be sent, and the agent's approval and ceiling are checked again at the moment of dispatch.")


class LegalDocument(models.Model):
    _inherit = 'legal.document'

    ai_classification = fields.Selection(help="How sensitive this document is. It is checked against the agent's allowed classifications, and a document marked blocked is never sent to any agent at all. New documents start as confidential.")


class LegalDocumentTemplate(models.Model):
    _inherit = 'legal.document.template'

    body = fields.Html(help="The firm's own drafting skeleton, used as the basis for generated documents.")
    allowed_fields = fields.Char(help="Comma-separated case fields this template is permitted to read.")


class LegalDocumentGeneration(models.Model):
    _inherit = 'legal.document.generation'

    request_id = fields.Many2one(help="The completed AI request whose output becomes the document. It has to be linked to a case.")
    template_id = fields.Many2one(help="Optional firm template the output is shaped against.")
    document_id = fields.Many2one(help="The legal document created from the output. It starts in draft with a confidential classification and still goes through the normal review cycle.")


class ResCompany(models.Model):
    _inherit = 'res.company'

    legal_ai_enabled = fields.Boolean(help="Master switch for this company. Off by default -- while it is off, no AI request can be sent no matter how it is configured.")

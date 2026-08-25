"""What each dispatch actually sent and got back.

Asking again reopens the request itself rather than branching a new one, which is
what a lawyer expects: change the instructions and ask the same question again.
But the payload, its hash and the answer are the record of what left the firm, and
reopening would write straight over them.

So a dispatch is snapshotted here before the request is reopened. The request
shows the latest exchange; this holds every earlier one, and neither can be edited
or deleted once written.
"""

from odoo import _, api, fields, models
from odoo.exceptions import AccessError


class LegalAIAttempt(models.Model):
    _name = 'legal.ai.attempt'
    _description = 'Legal AI Dispatch Record'
    _order = 'sent_at desc, id desc'

    request_id = fields.Many2one('legal.ai.request', required=True, ondelete='cascade', index=True)
    sequence_number = fields.Integer(string='Attempt', readonly=True)
    agent_id = fields.Many2one('ai.agent', readonly=True)
    charter_id = fields.Many2one('legal.ai.charter', string='Charter Applied', readonly=True)
    sent_at = fields.Datetime(readonly=True)
    consent_user_id = fields.Many2one('res.users', string='Consented By', readonly=True)
    consent_date = fields.Datetime(readonly=True)
    fields_sent = fields.Text(string='Data Shared', readonly=True)
    instructions_sent = fields.Text(string='Instructions', readonly=True)
    redacted_payload = fields.Text(string='Payload Sent', readonly=True)
    payload_hash = fields.Char(readonly=True)
    sanitized_response = fields.Html(string='Answer', readonly=True)

    def write(self, vals):
        raise AccessError(_('A dispatch record cannot be changed. It is the evidence of what left the firm.'))

    def unlink(self):
        raise AccessError(_('A dispatch record cannot be deleted.'))

    @api.model
    def _snapshot(self, request):
        """Preserve the dispatch currently sitting on the request."""
        if not request.payload_hash:
            return self.browse()
        return self.sudo().create({
            'request_id': request.id,
            'sequence_number': len(request.attempt_ids) + 1,
            'agent_id': request.agent_id.id,
            'charter_id': request.charter_id.id,
            'sent_at': request.write_date,
            'consent_user_id': request.consent_user_id.id,
            'consent_date': request.consent_date,
            'fields_sent': request.fields_sent,
            'instructions_sent': request.instructions_sent,
            'redacted_payload': request.redacted_payload,
            'payload_hash': request.payload_hash,
            'sanitized_response': request.sanitized_response,
        })

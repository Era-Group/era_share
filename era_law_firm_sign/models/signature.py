import hashlib
import hmac
import json
import secrets
import urllib.error
import urllib.request

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError, ValidationError


class LegalSignatureProvider(models.Model):
    _name = 'legal.signature.provider'
    _description = 'Signature Provider'
    _check_company_auto = True

    name = fields.Char(required=True)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company, index=True)
    provider_type = fields.Selection([('mock', 'Mock'), ('http', 'HTTP')], default='mock', required=True)
    endpoint_url = fields.Char()
    callback_secret = fields.Char(groups='base.group_system')
    timeout_seconds = fields.Integer(default=30)
    max_retries = fields.Integer(default=3)
    callback_tolerance_seconds = fields.Integer(default=300)

    @api.constrains('timeout_seconds', 'max_retries', 'callback_tolerance_seconds')
    def _check_limits(self):
        if any(p.timeout_seconds < 1 or p.max_retries < 0 or p.callback_tolerance_seconds < 30 for p in self):
            raise ValidationError(_('Provider limits are invalid.'))


class LegalSignatureRequest(models.Model):
    _name = 'legal.signature.request'
    _description = 'Legal Signature Request'
    _inherit = ['mail.thread']
    _check_company_auto = True

    engagement_id = fields.Many2one('legal.engagement', required=True, check_company=True)
    company_id = fields.Many2one(related='engagement_id.company_id', store=True, index=True)
    provider_id = fields.Many2one('legal.signature.provider', required=True, check_company=True)
    attachment_id = fields.Many2one('ir.attachment', required=True)
    external_reference = fields.Char(copy=False, index=True)
    document_hash = fields.Char(copy=False, readonly=True)
    nonce = fields.Char(default=lambda self: secrets.token_urlsafe(24), copy=False, readonly=True, index=True)
    sent_at = fields.Datetime(copy=False)
    signed_at = fields.Datetime(copy=False)
    retry_count = fields.Integer(copy=False)
    last_error = fields.Char(copy=False, readonly=True)
    state = fields.Selection([('draft', 'Draft'), ('sent', 'Sent'), ('signed', 'Signed'), ('failed', 'Failed'), ('cancelled', 'Cancelled')], default='draft', tracking=True)
    event_ids = fields.One2many('legal.signature.event', 'request_id', readonly=True)

    _external_unique = models.Constraint('UNIQUE(provider_id, external_reference)', 'Provider reference must be unique.')

    def _current_document_hash(self):
        self.ensure_one()
        return hashlib.sha256(self.attachment_id.raw or b'').hexdigest()

    def _prepare_provider_payload(self):
        self.ensure_one()
        return {'reference': self.external_reference, 'nonce': self.nonce, 'document_hash': self.document_hash, 'callback_url': '/legal/signature/callback'}

    def _event(self, event_type, event_key, metadata=None):
        return self.env['legal.signature.event'].sudo().create({'request_id': self.id, 'company_id': self.company_id.id, 'event_type': event_type, 'event_key': event_key, 'metadata': json.dumps(metadata or {}, sort_keys=True)[:4000]})

    def _send_to_provider(self):
        self.ensure_one()
        if self.provider_id.provider_type == 'mock':
            return {'accepted': True}
        if not self.provider_id.endpoint_url:
            raise UserError(_('The signature provider endpoint is not configured.'))
        payload = json.dumps(self._prepare_provider_payload()).encode()
        req = urllib.request.Request(self.provider_id.endpoint_url, data=payload, headers={'Content-Type': 'application/json'}, method='POST')
        try:
            with urllib.request.urlopen(req, timeout=self.provider_id.timeout_seconds) as response:
                return json.loads(response.read(100000).decode('utf-8', errors='replace') or '{}')
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            raise UserError(_('The signature provider could not accept the request.')) from exc

    def action_send(self):
        for rec in self:
            if rec.state not in ('draft', 'failed'):
                raise UserError(_('Only draft or failed requests can be sent.'))
            current_hash = rec._current_document_hash()
            if rec.document_hash and rec.document_hash != current_hash:
                raise UserError(_('The document changed after the signature request was prepared.'))
            if rec.provider_id.company_id != rec.company_id:
                raise UserError(_('The signature provider must belong to the request company.'))
            rec.write({'external_reference': rec.external_reference or f'SIGN-{rec.id}-{secrets.token_hex(6)}', 'document_hash': current_hash, 'last_error': False})
            try:
                rec._send_to_provider()
            except UserError as exc:
                rec.write({'state': 'failed', 'retry_count': rec.retry_count + 1, 'last_error': str(exc)[:500]})
                rec._event('failed', f'failed:{rec.retry_count}', {'reason': 'provider_error'})
                continue
            rec.write({'state': 'sent', 'sent_at': fields.Datetime.now()})
            rec._event('sent', f'sent:{rec.retry_count}', {'provider_type': rec.provider_id.provider_type})

    def _verify_callback_signature(self, payload, signature, timestamp=None):
        self.ensure_one()
        if not timestamp or not self.provider_id.callback_secret:
            return False
        try:
            stamp = fields.Datetime.to_datetime(timestamp)
        except (TypeError, ValueError):
            return False
        if abs((fields.Datetime.now() - stamp).total_seconds()) > self.provider_id.callback_tolerance_seconds:
            return False
        expected = hmac.new(self.provider_id.callback_secret.encode(), timestamp.encode() + b'.' + payload, hashlib.sha256).hexdigest()
        return bool(signature and hmac.compare_digest(expected, signature))

    def _process_callback(self, data):
        self.ensure_one()
        key = str(data.get('event_id') or '')
        if not key:
            raise UserError(_('A callback event identifier is required.'))
        if self.env['legal.signature.event'].sudo().search_count([('request_id', '=', self.id), ('event_key', '=', key)]):
            return False
        if self.state != 'sent' or data.get('reference') != self.external_reference or data.get('nonce') != self.nonce or data.get('document_hash') != self.document_hash:
            raise UserError(_('Signature callback does not match the request.'))
        status = data.get('status')
        self._event('callback', key, {'status': status})
        if status != 'signed':
            self.write({'state': 'failed', 'last_error': _('Provider rejected the signature request.')})
            return True
        self.write({'state': 'signed', 'signed_at': fields.Datetime.now(), 'last_error': False})
        self.env['legal.audit.log'].log(self, 'signature_callback', ['state', 'signed_at'])
        return True

    def action_retry(self):
        self.filtered(lambda r: r.state == 'failed' and r.retry_count < r.provider_id.max_retries).action_send()

    @api.model
    def _cron_retry_failed(self):
        self.search([('state', '=', 'failed')]).filtered(lambda r: r.retry_count < r.provider_id.max_retries).action_send()

    def action_cancel(self):
        if any(r.state == 'signed' for r in self):
            raise UserError(_('A signed request cannot be cancelled.'))
        self.write({'state': 'cancelled'})


class LegalSignatureEvent(models.Model):
    _name = 'legal.signature.event'
    _description = 'Legal Signature Event'
    _order = 'received_at desc, id desc'

    request_id = fields.Many2one('legal.signature.request', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one('res.company', required=True, index=True)
    event_type = fields.Selection([('sent', 'Sent'), ('failed', 'Failed'), ('callback', 'Callback')], required=True)
    event_key = fields.Char(required=True)
    received_at = fields.Datetime(default=fields.Datetime.now, required=True, readonly=True)
    metadata = fields.Text(readonly=True)

    _event_unique = models.Constraint('UNIQUE(request_id, event_key)', 'Callback event was already processed.')

    def write(self, vals):
        raise AccessError(_('Signature events cannot be modified.'))

    def unlink(self):
        raise AccessError(_('Signature events cannot be deleted.'))

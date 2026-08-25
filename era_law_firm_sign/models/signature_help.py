"""Field help text for the signature layer, declared incrementally."""

from odoo import fields, models


class LegalSignatureProvider(models.Model):
    _inherit = 'legal.signature.provider'

    provider_type = fields.Selection(help="Mock accepts everything locally and sends nothing anywhere. HTTP posts the request to the endpoint below.")
    endpoint_url = fields.Char(help="Where signature requests are posted for an HTTP provider.")
    callback_secret = fields.Char(help="Shared secret used to HMAC-sign callbacks. Without it every callback is rejected, so no request could ever complete. Visible to system administrators only.")
    timeout_seconds = fields.Integer(help="How long to wait for the provider to accept a request before treating it as failed.")
    max_retries = fields.Integer(help="How many times the scheduled job retries a failed request before leaving it alone.")
    callback_tolerance_seconds = fields.Integer(help="How far a callback's timestamp may be from now and still be accepted. This is what stops an old captured callback from being replayed later.")


class LegalSignatureRequest(models.Model):
    _inherit = 'legal.signature.request'

    engagement_id = fields.Many2one(help="The engagement letter being signed.")
    attachment_id = fields.Many2one(help="The exact file sent for signature. Its hash is frozen on dispatch.")
    external_reference = fields.Char(help="The provider's reference for this request, assigned on dispatch and used to match the callback back to it.")
    document_hash = fields.Char(help="SHA-256 of the file at the moment the request was prepared. If the file changes afterwards, sending is refused rather than signing a different text.")
    nonce = fields.Char(help="One-time value tied to this request. A callback that does not carry it is rejected, so a valid callback for one request cannot be reused for another.")
    sent_at = fields.Datetime(help="When the request was accepted by the provider.")
    signed_at = fields.Datetime(help="When a verified callback reported the document signed.")
    retry_count = fields.Integer(help="How many dispatch attempts have failed so far.")
    last_error = fields.Char(help="Why the last attempt failed.")
    state = fields.Selection(help="A signed request can no longer be cancelled. Failed requests are retried by the scheduled job up to the provider's limit.")
    event_ids = fields.One2many(help="Every dispatch, failure and callback recorded for this request. Events cannot be edited or deleted.")


class LegalSignatureEvent(models.Model):
    _inherit = 'legal.signature.event'

    event_type = fields.Selection(help="Whether this records a dispatch, a failure or an incoming callback.")
    event_key = fields.Char(help="The provider's event identifier, recorded once and only once. Re-delivering the same event changes nothing, which is what makes callbacks safe to retry.")
    received_at = fields.Datetime(help="When the event was recorded.")
    metadata = fields.Text(help="What the provider reported with the event.")

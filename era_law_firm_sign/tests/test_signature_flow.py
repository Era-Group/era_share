import hashlib
import hmac
from odoo.tests.common import TransactionCase, tagged
from odoo import fields

@tagged('post_install','-at_install')
class TestSignatureFlow(TransactionCase):
    def test_callback_signature(self):
        provider=self.env['legal.signature.provider'].create({'name':'Mock','callback_secret':'secret'})
        fake=self.env['legal.signature.request'].new({'provider_id':provider.id})
        payload=b'{"status":"signed"}'
        timestamp=fields.Datetime.to_string(fields.Datetime.now())
        signature=hmac.new(b'secret',timestamp.encode()+b'.'+payload,hashlib.sha256).hexdigest()
        self.assertTrue(fake._verify_callback_signature(payload,signature,timestamp))
        self.assertFalse(fake._verify_callback_signature(payload,'bad',timestamp))
        self.assertFalse(fake._verify_callback_signature(payload,signature,'2000-01-01T00:00:00'))

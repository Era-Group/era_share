from odoo.tests.common import TransactionCase, tagged

@tagged('post_install','-at_install')
class TestAIRedaction(TransactionCase):
    def test_sensitive_values_are_redacted(self):
        value=self.env['legal.ai.request']._redact('id 1234567890 phone 0551234567 email a@example.com')
        self.assertNotIn('1234567890',value)
        self.assertNotIn('0551234567',value)
        self.assertNotIn('a@example.com',value)

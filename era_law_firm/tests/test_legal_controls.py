from odoo.tests.common import TransactionCase, tagged
from odoo.exceptions import ValidationError

@tagged('post_install','-at_install')
class TestLegalControls(TransactionCase):
    def test_arabic_normalization_and_deadline_rule(self):
        check=self.env['legal.conflict.check']
        self.assertEqual(check._normalize_arabic_text('  أَحْمــد  '),'احمد')
        with self.assertRaises(ValidationError):
            self.env['legal.deadline.rule'].create({'name':'Invalid','days':0,'start_point':'manual','legal_reference':'test'})

    def test_audit_is_immutable(self):
        log=self.env['legal.audit.log'].create({'model_name':'res.partner','res_id':1,'operation':'test'})
        with self.assertRaises(Exception):log.unlink()

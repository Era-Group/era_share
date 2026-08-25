"""Auto-provisioning of the Client Trust Accounting settings."""

from odoo.tests.common import tagged

from .common import LegalCommon


@tagged('post_install', '-at_install')
class TestTrustSetup(LegalCommon):

    def _clear(self, company):
        company.write({
            'legal_trust_journal_id': False,
            'legal_trust_liability_account_id': False,
            'legal_trust_bank_account_id': False,
            'legal_trust_receivable_account_id': False,
        })

    def test_setup_fills_every_empty_setting(self):
        self._clear(self.company)
        self.company._setup_legal_trust_accounting()
        self.assertTrue(self.company.legal_trust_journal_id)
        self.assertTrue(self.company.legal_trust_liability_account_id)
        self.assertTrue(self.company.legal_trust_bank_account_id)
        self.assertTrue(self.company.legal_trust_receivable_account_id)

    def test_each_setting_gets_the_right_account_type(self):
        self._clear(self.company)
        self.company._setup_legal_trust_accounting()
        self.assertEqual(self.company.legal_trust_liability_account_id.account_type, 'liability_current')
        self.assertEqual(self.company.legal_trust_bank_account_id.account_type, 'asset_cash')
        self.assertEqual(self.company.legal_trust_receivable_account_id.account_type, 'asset_receivable')
        self.assertEqual(self.company.legal_trust_journal_id.type, 'general')

    def test_receivable_matches_the_one_invoices_use(self):
        """Otherwise applying trust funds cannot reconcile against the invoice."""
        self._clear(self.company)
        self.company._setup_legal_trust_accounting()
        expected = self.env['res.partner'].with_company(self.company).new({}).property_account_receivable_id
        if expected:
            self.assertEqual(self.company.legal_trust_receivable_account_id, expected)

    def test_an_existing_configuration_is_never_overwritten(self):
        chosen = self.env['account.account'].create({
            'code': self._free_code('liability_current', '201000'),
            'name': 'Hand-picked Trust Liability',
            'account_type': 'liability_current', 'company_ids': [(4, self.company.id)]})
        self.company.legal_trust_liability_account_id = chosen
        self.company.legal_trust_journal_id = False
        self.company._setup_legal_trust_accounting()
        self.assertEqual(self.company.legal_trust_liability_account_id, chosen)
        self.assertTrue(self.company.legal_trust_journal_id, 'the empty setting should still be filled')

    def test_running_twice_creates_nothing_new(self):
        self._clear(self.company)
        self.company._setup_legal_trust_accounting()
        before = (self.env['account.account'].search_count([]),
                  self.env['account.journal'].search_count([]),
                  self.company.legal_trust_journal_id.id,
                  self.company.legal_trust_liability_account_id.id,
                  self.company.legal_trust_bank_account_id.id)
        self.company._setup_legal_trust_accounting()
        after = (self.env['account.account'].search_count([]),
                 self.env['account.journal'].search_count([]),
                 self.company.legal_trust_journal_id.id,
                 self.company.legal_trust_liability_account_id.id,
                 self.company.legal_trust_bank_account_id.id)
        self.assertEqual(before, after)

    def test_cleared_settings_reuse_the_records_already_created(self):
        self._clear(self.company)
        self.company._setup_legal_trust_accounting()
        liability = self.company.legal_trust_liability_account_id
        self._clear(self.company)
        self.company._setup_legal_trust_accounting()
        self.assertEqual(self.company.legal_trust_liability_account_id, liability,
                         'a second pass must reuse the record, not create a duplicate')

    def test_a_company_without_a_chart_is_skipped(self):
        blank = self.env['res.company'].create({'name': 'Chartless Firm'})
        blank._setup_legal_trust_accounting()
        self.assertFalse(blank.legal_trust_liability_account_id)
        self.assertFalse(blank.legal_trust_journal_id)

    def test_the_provisioned_setup_can_actually_post(self):
        """The point of the defaults: a deposit works with no manual configuration."""
        self._clear(self.company)
        self.company._setup_legal_trust_accounting()
        trust = self.env['legal.trust.account'].create({
            'partner_id': self.env['res.partner'].create({'name': 'Fresh Client'}).id,
            'company_id': self.company.id})
        self.env['legal.trust.operation.wizard'].create({
            'trust_account_id': trust.id, 'transaction_type': 'deposit',
            'amount': 2500, 'reference': 'Opening deposit'}).action_apply()
        trust.invalidate_recordset()
        self.assertEqual(trust.available_balance, 2500)
        self.assertEqual(trust.transaction_ids.move_id.journal_id, self.company.legal_trust_journal_id)

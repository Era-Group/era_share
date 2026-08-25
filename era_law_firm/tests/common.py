"""Shared fixtures.

Uses whatever the module provisions for Client Trust Accounting, so the suite
exercises the real defaults. When the database has no chart of accounts at all,
it falls back to building the minimum by hand -- never with hard-coded codes,
which would collide with whatever chart happens to be installed.
"""

from odoo import fields
from odoo.tests.common import TransactionCase


class LegalCommon(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.env.user.group_ids = [(4, cls.env.ref('era_law_firm.group_legal_manager').id)]

        cls.company._setup_legal_trust_accounting()
        if not cls.company.legal_trust_journal_id:
            cls._build_minimum_accounting()

        cls.acc_liability = cls.company.legal_trust_liability_account_id
        cls.acc_bank = cls.company.legal_trust_bank_account_id
        cls.acc_receivable = cls.company.legal_trust_receivable_account_id
        cls.trust_journal = cls.company.legal_trust_journal_id

        cls.acc_income = cls._account('income', 'Legal Fees Income', '400000')
        cls.sale_journals = cls.env['account.journal'].search(
            [('type', '=', 'sale'), ('company_id', '=', cls.company.id)])
        if not cls.sale_journals:
            cls.sale_journals = cls.env['account.journal'].create({
                'name': 'Customer Invoices', 'code': cls._journal_code('LINV'),
                'type': 'sale', 'company_id': cls.company.id})
        # ZATCA clearance belongs to l10n_sa_edi, not to this module. Detaching the EDI
        # formats keeps these tests about legal billing instead of e-invoicing onboarding.
        cls.sale_journals.edi_format_ids = [(5, 0, 0)]

        cls.product = cls.env['product.product'].create({
            'name': 'Legal Services', 'type': 'service',
            'property_account_income_id': cls.acc_income.id, 'taxes_id': [(5, 0, 0)]})

        cls.lawyer = cls.env['res.users'].create({
            'name': 'Test Lawyer', 'login': 'legal_test_lawyer', 'company_id': cls.company.id,
            'group_ids': [(4, cls.env.ref('era_law_firm.group_legal_lawyer').id)]})

        cls.client = cls.env['res.partner'].create({
            'name': 'Al-Rashid Trading Co', 'email': 'client@example.com',
            'property_account_receivable_id': cls.acc_receivable.id})
        cls.opponent = cls.env['res.partner'].create({'name': 'Opposing Party LLC'})

    # ------------------------------------------------------------------
    # fixture helpers
    # ------------------------------------------------------------------

    @classmethod
    def _free_code(cls, account_type, fallback):
        Account = cls.env['account.account'].with_company(cls.company)
        codes = sorted(c for c in Account.search([('account_type', '=', account_type)]).mapped('code') if c)
        return Account._search_new_account_code(codes[-1] if codes else fallback)

    @classmethod
    def _account(cls, account_type, name, fallback_code, reuse=True):
        Account = cls.env['account.account'].with_company(cls.company)
        if reuse:
            existing = Account.search([('account_type', '=', account_type)], limit=1)
            if existing:
                return existing
        return Account.create({
            'name': name, 'code': cls._free_code(account_type, fallback_code),
            'account_type': account_type, 'company_ids': [(4, cls.company.id)]})

    @classmethod
    def _journal_code(cls, preferred):
        taken = set(cls.env['account.journal'].with_context(active_test=False).search(
            [('company_id', '=', cls.company.id)]).mapped('code'))
        if preferred not in taken:
            return preferred
        return next(c for c in (f'{preferred[:3]}{n}' for n in range(1, 100)) if c not in taken)

    @classmethod
    def _build_minimum_accounting(cls):
        """Only reached on a database with no chart of accounts."""
        liability = cls._account('liability_current', 'Client Trust Liability', '201000', reuse=False)
        bank = cls._account('asset_cash', 'Client Trust Bank', '101000', reuse=False)
        receivable = cls._account('asset_receivable', 'Trade Receivable', '102000', reuse=False)
        receivable.reconcile = True
        journal = cls.env['account.journal'].create({
            'name': 'Client Trust', 'code': cls._journal_code('TRST'),
            'type': 'general', 'company_id': cls.company.id})
        cls.company.write({
            'legal_trust_journal_id': journal.id,
            'legal_trust_liability_account_id': liability.id,
            'legal_trust_bank_account_id': bank.id,
            'legal_trust_receivable_account_id': receivable.id,
        })

    # ------------------------------------------------------------------
    # record builders
    # ------------------------------------------------------------------

    @classmethod
    def _make_case(cls, **overrides):
        values = {
            'client_id': cls.client.id, 'lawyer_id': cls.lawyer.id, 'case_type': 'litigation',
            'stage_id': cls.env.ref('era_law_firm.stage_intake').id, 'company_id': cls.company.id,
            'claim_amount': 500000, 'date_filed': fields.Date.today(),
        }
        values.update(overrides)
        return cls.env['legal.case'].create(values)

    @classmethod
    def _confirmed_case(cls):
        case = cls._make_case()
        cls.env['legal.case.party'].create({
            'case_id': case.id, 'partner_id': cls.opponent.id, 'role': 'opponent'})
        case.action_run_conflict_check()
        case.action_confirm()
        return case

    @classmethod
    def _active_engagement(cls, case, **overrides):
        values = {
            'name': 'Engagement Letter', 'case_id': case.id, 'billing_type': 'hourly',
            'hourly_rate': 800, 'product_id': cls.product.id,
        }
        values.update(overrides)
        engagement = cls.env['legal.engagement'].create(values)
        engagement.action_activate()
        return engagement

    @classmethod
    def _trust_account(cls, partner=None):
        return cls.env['legal.trust.account'].create({
            'partner_id': (partner or cls.client).id, 'company_id': cls.company.id})

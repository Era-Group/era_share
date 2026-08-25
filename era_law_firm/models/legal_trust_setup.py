"""Provision the Client Trust Accounting settings so the module works out of the box.

Runs on install and on every update (see data/legal_trust_setup_data.xml). It only
ever fills settings that are empty -- an existing configuration is never touched.
"""

import logging

from odoo import _, api, models

_logger = logging.getLogger(__name__)

# (settings field, xmlid suffix, account name, account type, fallback code)
TRUST_ACCOUNTS = [
    ('legal_trust_liability_account_id', 'trust_liability_account',
     'Client Trust Liability', 'liability_current', '201000'),
    ('legal_trust_bank_account_id', 'trust_bank_account',
     'Client Trust Bank', 'asset_cash', '101000'),
]


class ResCompany(models.Model):
    _inherit = 'res.company'

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _legal_trust_xmlid(self, suffix):
        """Deterministic xmlid so repeated upgrades reuse the same record."""
        self.ensure_one()
        return f'era_law_firm.{suffix}_company_{self.id}'

    def _legal_trust_existing(self, suffix):
        self.ensure_one()
        return self.env.ref(self._legal_trust_xmlid(suffix), raise_if_not_found=False)

    def _legal_trust_register(self, suffix, record):
        self.ensure_one()
        self.env['ir.model.data'].create({
            'name': f'{suffix}_company_{self.id}',
            'module': 'era_law_firm',
            'model': record._name,
            'res_id': record.id,
            'noupdate': True,
        })
        return record

    def _legal_trust_default_receivable(self):
        """The receivable the customer invoices already use.

        Applying trust funds reconciles the trust entry against the invoice's
        receivable line, and Odoo only reconciles within a single account -- so this
        must be the company's own receivable, never a dedicated one.
        """
        self.ensure_one()
        account = self.env['res.partner'].with_company(self).new({}).property_account_receivable_id
        if not account:
            account = self.env['account.account'].with_company(self).search(
                [('account_type', '=', 'asset_receivable'), ('deprecated', '=', False)],
                order='id', limit=1)
        return account

    def _legal_trust_next_code(self, account_type, fallback):
        """Pick a free code that sits with its peers in whatever chart is installed."""
        self.ensure_one()
        Account = self.env['account.account'].with_company(self)
        codes = sorted(code for code in Account.search(
            [('account_type', '=', account_type)]).mapped('code') if code)
        return Account._search_new_account_code(codes[-1] if codes else fallback)

    def _legal_trust_build_account(self, suffix, name, account_type, fallback_code):
        self.ensure_one()
        account = self._legal_trust_existing(suffix)
        if account:
            return account
        account = self.env['account.account'].with_company(self).create({
            'name': name,
            'code': self._legal_trust_next_code(account_type, fallback_code),
            'account_type': account_type,
            'reconcile': account_type == 'asset_receivable',
            'company_ids': [(4, self.id)],
        })
        return self._legal_trust_register(suffix, account)

    def _legal_trust_build_journal(self):
        self.ensure_one()
        journal = self._legal_trust_existing('trust_journal')
        if journal:
            return journal
        Journal = self.env['account.journal']
        code = 'TRST'
        taken = set(Journal.with_context(active_test=False).search(
            [('company_id', '=', self.id)]).mapped('code'))
        if code in taken:
            code = next((c for c in (f'TRS{n}' for n in range(1, 100)) if c not in taken), 'TRST')
        journal = Journal.create({
            'name': _('Client Trust'),
            'code': code,
            'type': 'general',
            'company_id': self.id,
        })
        return self._legal_trust_register('trust_journal', journal)

    # ------------------------------------------------------------------
    # provisioning
    # ------------------------------------------------------------------

    def _setup_legal_trust_accounting(self):
        """Fill every empty Client Trust Accounting setting. Never overwrites."""
        for company in self:
            if not self.env['account.account'].with_company(company).search_count(
                    [('company_ids', 'in', company.id)], limit=1):
                _logger.info(
                    'era_law_firm: company %s has no chart of accounts yet, '
                    'skipping trust accounting setup', company.display_name)
                continue

            values = {}
            for field_name, suffix, name, account_type, fallback in TRUST_ACCOUNTS:
                if not company[field_name]:
                    values[field_name] = company._legal_trust_build_account(
                        suffix, name, account_type, fallback).id

            if not company.legal_trust_receivable_account_id:
                receivable = company._legal_trust_default_receivable()
                if receivable:
                    values['legal_trust_receivable_account_id'] = receivable.id
                else:
                    _logger.warning(
                        'era_law_firm: company %s has no receivable account, '
                        'trust settlement will stay unconfigured', company.display_name)

            if not company.legal_trust_journal_id:
                values['legal_trust_journal_id'] = company._legal_trust_build_journal().id

            if values:
                company.write(values)
                _logger.info('era_law_firm: configured trust accounting for %s (%s)',
                             company.display_name, ', '.join(sorted(values)))

    @api.model
    def _setup_legal_trust_accounting_all(self):
        """Entry point for the data file: runs at install and at every update."""
        self.search([])._setup_legal_trust_accounting()

    @api.model_create_multi
    def create(self, vals_list):
        companies = super().create(vals_list)
        companies._setup_legal_trust_accounting()
        return companies

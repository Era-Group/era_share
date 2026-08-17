from odoo import Command
from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import CommissionCommon


@tagged('post_install', '-at_install')
class TestCommissionSettlement(CommissionCommon):
    """From computed lines to a posted journal entry -- and back out again."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.account_expense = cls.env['account.account'].create({
            'name': 'Commission Expense',
            'code': 'ERACOMEXP',
            'account_type': 'expense',
        })
        cls.account_payable = cls.env['account.account'].create({
            'name': 'Commission Payable',
            'code': 'ERACOMPAY',
            'account_type': 'liability_payable',
            'reconcile': True,
        })
        cls.journal = cls.env['account.journal'].create({
            'name': 'Commissions',
            'code': 'ERACO',
            'type': 'general',
        })
        cls.company.write({
            'era_commission_journal_id': cls.journal.id,
            'era_commission_expense_account_id': cls.account_expense.id,
            'era_commission_payable_account_id': cls.account_payable.id,
        })

    def _settlement(self, agent=None):
        return self.env['era.commission.settlement'].create({
            'agent_id': (agent or self.agent).id,
            'date_from': self.date_from,
            'date_to': self.date_to,
        })

    def test_full_cycle_posts_a_balanced_entry(self):
        order = self._make_order([(self.product_fabric, 10, 100.0)])
        self._make_invoice(order)

        settlement = self._settlement()
        settlement.action_compute()
        self.assertAlmostEqual(settlement.amount_total, 50.0)
        self.assertEqual(settlement.line_ids.mapped('state'), ['confirmed'])

        settlement.action_submit()
        self.assertEqual(settlement.state, 'to_approve')

        settlement.action_approve()
        self.assertEqual(settlement.state, 'approved')
        self.assertEqual(settlement.line_ids.mapped('state'), ['settled'])
        self.assertEqual(settlement.approved_by_id, self.env.user)

        settlement.action_post()
        move = settlement.move_id
        self.assertEqual(settlement.state, 'posted')
        self.assertEqual(move.state, 'posted')
        self.assertEqual(move.journal_id, self.journal)
        self.assertAlmostEqual(sum(move.line_ids.mapped('debit')),
                               sum(move.line_ids.mapped('credit')))
        self.assertAlmostEqual(sum(move.line_ids.mapped('debit')), 50.0)
        self.assertIn(self.account_expense, move.line_ids.account_id)
        self.assertIn(self.account_payable, move.line_ids.account_id)

    def test_recomputation_never_touches_a_settled_line(self):
        order = self._make_order([(self.product_fabric, 10, 100.0)])
        self._make_invoice(order)
        settlement = self._settlement()
        settlement.action_compute()
        settlement.action_approve()
        line = settlement.line_ids
        self.assertEqual(len(line), 1)

        self.Engine.generate(self.date_from, self.date_to)
        line.invalidate_recordset()
        self.assertAlmostEqual(line.commission_amount, 50.0)
        self.assertEqual(line.state, 'settled')
        self.assertEqual(len(self._lines(self.agent)), 1)

    def test_a_settled_line_refuses_to_be_edited(self):
        order = self._make_order([(self.product_fabric, 10, 100.0)])
        self._make_invoice(order)
        settlement = self._settlement()
        settlement.action_compute()
        settlement.action_approve()
        with self.assertRaises(UserError):
            settlement.line_ids.write({'rate': 1.0})

    def test_cancelling_the_invoice_afterwards_claws_the_money_back(self):
        order = self._make_order([(self.product_fabric, 10, 100.0)])
        invoice = self._make_invoice(order)
        settlement = self._settlement()
        settlement.action_compute()
        settlement.action_approve()

        invoice.button_draft()
        invoice.button_cancel()
        self.Engine.generate(self.date_from, self.date_to)

        reversals = self._lines(self.agent, line_type='reversal')
        self.assertEqual(len(reversals), 1)
        self.assertAlmostEqual(reversals.commission_amount, -50.0)
        self.assertEqual(reversals.state, 'draft')
        self.assertEqual(reversals.reversed_line_id, settlement.line_ids)
        # and running it again does not stack a second claw-back
        self.Engine.generate(self.date_from, self.date_to)
        self.assertEqual(len(self._lines(self.agent, line_type='reversal')), 1)

    def test_an_approved_settlement_cannot_be_deleted(self):
        order = self._make_order([(self.product_fabric, 10, 100.0)])
        self._make_invoice(order)
        settlement = self._settlement()
        settlement.action_compute()
        settlement.action_approve()
        with self.assertRaises(UserError):
            settlement.unlink()

    def test_cancelling_frees_the_lines_again(self):
        order = self._make_order([(self.product_fabric, 10, 100.0)])
        self._make_invoice(order)
        settlement = self._settlement()
        settlement.action_compute()
        settlement.action_approve()
        lines = settlement.line_ids

        settlement.action_cancel()
        self.assertEqual(settlement.state, 'cancel')
        self.assertEqual(lines.mapped('state'), ['draft'])
        self.assertFalse(lines.settlement_id)

    def test_adjustments_move_the_net(self):
        order = self._make_order([(self.product_fabric, 10, 100.0)])
        self._make_invoice(order)
        settlement = self._settlement()
        settlement.action_compute()
        settlement.adjustment_ids = [
            Command.create({'name': 'Ramadan bonus', 'type': 'bonus', 'amount': 30.0}),
            Command.create({'name': 'Fuel advance', 'type': 'deduction', 'amount': 10.0}),
        ]
        self.assertAlmostEqual(settlement.amount_adjustment, 20.0)
        self.assertAlmostEqual(settlement.amount_total, 70.0)

    def test_posting_without_the_accounts_says_so(self):
        self.company.era_commission_expense_account_id = False
        order = self._make_order([(self.product_fabric, 10, 100.0)])
        self._make_invoice(order)
        settlement = self._settlement()
        settlement.action_compute()
        settlement.action_approve()
        with self.assertRaises(UserError):
            settlement.action_post()

    def test_vendor_bill_payout(self):
        order = self._make_order([(self.product_fabric, 10, 100.0)])
        self._make_invoice(order)
        settlement = self._settlement()
        settlement.payout_mode = 'bill'
        settlement.action_compute()
        settlement.action_approve()
        settlement.action_post()

        bill = settlement.move_id
        self.assertEqual(bill.move_type, 'in_invoice')
        self.assertEqual(bill.partner_id, self.agent_partner)
        self.assertAlmostEqual(bill.amount_total, 50.0)

    def test_generate_wizard_opens_one_settlement_per_agent(self):
        order = self._make_order([(self.product_fabric, 10, 100.0)])
        self._make_invoice(order)
        wizard = self.env['era.commission.generate'].create({
            'date_from': self.date_from,
            'date_to': self.date_to,
        })
        action = wizard.action_generate()
        settlements = self.env['era.commission.settlement'].search(action['domain'])
        # the agent and the manager who earns an override on them
        self.assertEqual(settlements.agent_id, self.agent + self.manager)
        self.assertAlmostEqual(
            sum(settlements.mapped('amount_total')), 55.0)

    def test_the_statement_renders(self):
        order = self._make_order([(self.product_fabric, 10, 100.0)])
        self._make_invoice(order)
        settlement = self._settlement()
        settlement.action_compute()
        # a test run renders html unless it is asked for the real thing
        pdf, _type = self.env['ir.actions.report'].with_context(
            force_report_rendering=True
        )._render_qweb_pdf(
            'era_sales_commission.action_report_commission_statement',
            settlement.ids)
        self.assertTrue(pdf.startswith(b'%PDF'))

    def test_the_total_of_the_rep_gathers_every_commission_type(self):
        """One number goes to payroll, whatever the money was earned on."""
        rep = self.env['era.commission.agent'].create({'name': 'All Round Rep'})
        self._rate(rep, 'sales', rate=2.0)
        self._rate(rep, 'collection', rate=1.0)
        self._rate(rep, 'qty_sold', unit_price=0.5, product=self.product_button)

        sales = self.env['era.commission.plan'].create({
            'name': 'S', 'basis': 'sales', 'deduct_tax': False, 'rule_ids': []})
        collection = self.env['era.commission.plan'].create({
            'name': 'C', 'basis': 'collection', 'deduct_tax': False,
            'rule_ids': []})
        quantity = self.env['era.commission.plan'].create({
            'name': 'Q', 'basis': 'qty_sold', 'rule_ids': []})
        for plan in (sales, collection, quantity):
            self._assign(rep, plan)
            plan.action_approve()

        order = self._make_order([(self.product_button, 100, 10.0)], agent=rep)
        invoice = self._make_invoice(order)
        self._pay(invoice, 1000.0)

        settlement = self._settlement(agent=rep)
        settlement.action_compute()

        # 1000 x 2% + 1000 x 1% + 100 units x 0.5
        self.assertAlmostEqual(settlement.era_rep_total_commission, 80.0)
        self.assertAlmostEqual(settlement.amount_total, 80.0)
        self.assertEqual(
            set(settlement.line_ids.mapped('commission_type')),
            {'sales', 'collection', 'qty_sold'})
        self.assertIn('Commission on Sales', settlement.commission_type_summary)

    def test_simulation_writes_nothing(self):
        order = self._make_order([(self.product_fabric, 10, 100.0)])
        self._make_invoice(order)
        wizard = self.env['era.commission.simulate'].create({
            'agent_id': self.agent.id,
            'date_from': self.date_from,
            'date_to': self.date_to,
        })
        wizard.action_simulate()
        self.assertAlmostEqual(wizard.amount_total, 50.0)
        self.assertFalse(self._lines(self.agent))

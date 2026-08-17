from odoo import Command
from odoo.tests import tagged

from .common import CommissionCommon


@tagged('post_install', '-at_install')
class TestCommissionCollection(CommissionCommon):
    """Commission on the money actually collected."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.plan_cash = cls._new_plan(
            'On Collection', basis='collection', rule_ids=[Command.create({
                'name': '10% on cash in', 'calc_type': 'percent', 'rate': 10.0,
            })])
        cls.cash_agent = cls.env['era.commission.agent'].create({
            'name': 'Collector', 'partner_id': cls.agent_partner.id})
        cls._assign(cls.cash_agent, cls.plan_cash)

    def _sold_and_invoiced(self):
        order = self._make_order(
            [(self.product_fabric, 10, 100.0)], agent=self.cash_agent)
        return self._make_invoice(order)

    def test_partial_payment_earns_its_share(self):
        invoice = self._sold_and_invoiced()
        self._pay(invoice, 400.0)

        self.Engine.generate(self.date_from, self.date_to, plans=self.plan_cash)
        lines = self._lines(self.cash_agent)
        self.assertEqual(len(lines), 1)
        # 40% collected on 1000 -> base 400 -> 10% -> 40
        self.assertAlmostEqual(lines.base_amount, 400.0)
        self.assertAlmostEqual(lines.commission_amount, 40.0)

    def test_second_payment_adds_a_line_and_leaves_the_first_alone(self):
        invoice = self._sold_and_invoiced()
        self._pay(invoice, 400.0)
        self.Engine.generate(self.date_from, self.date_to, plans=self.plan_cash)
        first = self._lines(self.cash_agent)
        first_key = first.origin_key

        self._pay(invoice, 600.0)
        self.Engine.generate(self.date_from, self.date_to, plans=self.plan_cash)
        lines = self._lines(self.cash_agent)

        self.assertEqual(len(lines), 2)
        untouched = lines.filtered(lambda line: line.origin_key == first_key)
        self.assertAlmostEqual(untouched.commission_amount, 40.0)
        self.assertAlmostEqual(sum(lines.mapped('commission_amount')), 100.0)

    def test_credit_note_reconciled_against_the_invoice_is_not_cash(self):
        """No money moved, so nothing is earned -- and nothing counted twice."""
        invoice = self._sold_and_invoiced()
        reversal = invoice._reverse_moves([{
            'invoice_date': self.order_date, 'date': self.order_date,
        }])
        reversal.action_post()
        open_lines = (invoice + reversal).line_ids.filtered(
            lambda line: line.display_type == 'payment_term' and not line.reconciled)
        if open_lines:
            open_lines.reconcile()

        # the matching really exists -- it is skipped on purpose, not missing
        partials = self.env['account.partial.reconcile'].search([
            ('debit_move_id.move_id', 'in', (invoice + reversal).ids),
            ('credit_move_id.move_id', 'in', (invoice + reversal).ids),
        ])
        self.assertTrue(partials)

        self.Engine.generate(self.date_from, self.date_to, plans=self.plan_cash)
        self.assertFalse(self._lines(self.cash_agent))

    def test_generating_twice_does_not_double(self):
        invoice = self._sold_and_invoiced()
        self._pay(invoice, 1000.0)
        self.Engine.generate(self.date_from, self.date_to, plans=self.plan_cash)
        self.Engine.generate(self.date_from, self.date_to, plans=self.plan_cash)
        lines = self._lines(self.cash_agent)
        self.assertEqual(len(lines), 1)
        self.assertAlmostEqual(lines.commission_amount, 100.0)
        self.assertEqual(len(set(lines.mapped('origin_key'))), 1)

    def test_the_payment_is_recorded_on_the_line(self):
        invoice = self._sold_and_invoiced()
        payment = self._pay(invoice, 1000.0)
        self.Engine.generate(self.date_from, self.date_to, plans=self.plan_cash)
        line = self._lines(self.cash_agent)
        self.assertEqual(line.payment_id, payment)
        self.assertTrue(line.partial_id)

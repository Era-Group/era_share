from odoo import Command
from odoo.tests import tagged

from .common import CommissionCommon


@tagged('post_install', '-at_install')
class TestCommissionRules(CommissionCommon):
    """The arithmetic of a rule, and which rule a line falls under."""

    def test_percentage_per_category(self):
        """The most specific rule wins because it is first in the sequence."""
        order = self._make_order([
            (self.product_fabric, 10, 100.0),
            (self.product_button, 100, 10.0),
        ])
        self._make_invoice(order)
        self.Engine.generate(self.date_from, self.date_to)

        lines = self._lines(self.agent)
        self.assertEqual(len(lines), 2)
        fabric = lines.filtered(lambda line: line.product_id == self.product_fabric)
        button = lines.filtered(lambda line: line.product_id == self.product_button)
        self.assertAlmostEqual(fabric.commission_amount, 50.0)   # 1000 x 5%
        self.assertAlmostEqual(button.commission_amount, 20.0)   # 1000 x 2%

    def test_fixed_per_unit_and_per_document(self):
        plan_qty = self._new_plan('Per Unit', rule_ids=[Command.create({
            'name': '3 per unit', 'calc_type': 'fixed_qty', 'amount_fixed': 3.0,
        })])
        agent = self.env['era.commission.agent'].create({'name': 'Unit Rep'})
        self._assign(agent, plan_qty)
        order = self._make_order([(self.product_fabric, 7, 100.0)], agent=agent)
        self._make_invoice(order)

        self.Engine.generate(self.date_from, self.date_to, plans=plan_qty)
        self.assertAlmostEqual(self._lines(agent).commission_amount, 21.0)

    def test_progressive_versus_flat_tiers(self):
        tiers = [
            Command.create({'base_from': 0.0, 'rate': 2.0}),
            Command.create({'base_from': 1000.0, 'rate': 5.0}),
        ]
        rule_vals = {
            'name': 'Tiered', 'calc_type': 'tier', 'tier_ids': tiers,
        }
        progressive = self._new_plan(
            'Progressive', rule_ids=[Command.create(
                dict(rule_vals, tier_mode='progressive'))])
        flat = self._new_plan(
            'Flat', rule_ids=[Command.create(dict(rule_vals, tier_mode='flat'))])

        rule_p = progressive.rule_ids
        rule_f = flat.rule_ids
        # 1500: 1000 at 2% + 500 at 5% = 45 progressive, 1500 at 5% = 75 flat
        self.assertAlmostEqual(rule_p._compute_amount(1500.0), 45.0)
        self.assertAlmostEqual(rule_f._compute_amount(1500.0), 75.0)
        # below the second threshold both agree
        self.assertAlmostEqual(rule_p._compute_amount(800.0), 16.0)
        self.assertAlmostEqual(rule_f._compute_amount(800.0), 16.0)

    def test_cap_and_minimum(self):
        plan = self._new_plan('Capped', rule_ids=[
            Command.create({
                'name': 'Big deals only', 'sequence': 10, 'calc_type': 'percent',
                'rate': 10.0, 'min_base': 5000.0, 'amount_max': 300.0,
            }),
            Command.create({
                'name': 'Everything else', 'sequence': 20, 'calc_type': 'percent',
                'rate': 1.0,
            }),
        ])
        agent = self.env['era.commission.agent'].create({'name': 'Capped Rep'})
        self._assign(agent, plan)
        order = self._make_order([
            (self.product_fabric, 60, 100.0),   # 6000 -> first rule, capped at 300
            (self.product_button, 10, 10.0),    # 100 -> below the minimum, 1%
        ], agent=agent)
        self._make_invoice(order)
        self.Engine.generate(self.date_from, self.date_to, plans=plan)

        lines = self._lines(agent)
        big = lines.filtered(lambda line: line.product_id == self.product_fabric)
        small = lines.filtered(lambda line: line.product_id == self.product_button)
        self.assertAlmostEqual(big.commission_amount, 300.0)
        self.assertAlmostEqual(small.commission_amount, 1.0)

    def test_excluded_product_and_downpayment(self):
        self.plan.excluded_product_ids = self.product_shipping
        order = self._make_order([
            (self.product_fabric, 10, 100.0),
            (self.product_shipping, 1, 50.0),
        ])
        self._make_invoice(order)
        self.Engine.generate(self.date_from, self.date_to)

        lines = self._lines(self.agent)
        self.assertEqual(lines.product_id, self.product_fabric)
        self.assertAlmostEqual(sum(lines.mapped('commission_amount')), 50.0)

    def test_margin_basis(self):
        plan = self._new_plan('Margin', basis='margin', rule_ids=[Command.create({
            'name': '20% of margin', 'calc_type': 'percent', 'rate': 20.0,
            'base_field': 'margin',
        })])
        agent = self.env['era.commission.agent'].create({'name': 'Margin Rep'})
        self._assign(agent, plan)
        order = self._make_order([(self.product_fabric, 10, 100.0)], agent=agent)
        self._make_invoice(order)
        self.Engine.generate(self.date_from, self.date_to, plans=plan)

        # 1000 sold, 600 of cost -> 400 of margin -> 80
        self.assertAlmostEqual(self._lines(agent).commission_amount, 80.0)

    def test_override_for_the_manager(self):
        order = self._make_order([(self.product_fabric, 10, 100.0)])
        self._make_invoice(order)
        self.Engine.generate(self.date_from, self.date_to)

        override = self._lines(self.manager, line_type='override')
        self.assertEqual(len(override), 1)
        self.assertAlmostEqual(override.commission_amount, 5.0)   # 10% of the agent's 50
        self.assertEqual(override.parent_line_id.agent_id, self.agent)

    def test_document_share_splits_the_base(self):
        second = self.env['era.commission.agent'].create({'name': 'Second Rep'})
        self._assign(second, self.plan)
        order = self._make_order([(self.product_fabric, 10, 100.0)])
        order.era_agent_share_ids = [
            Command.create({'agent_id': self.agent.id, 'share': 60.0}),
            Command.create({'agent_id': second.id, 'share': 40.0}),
        ]
        invoice = self._make_invoice(order)
        self.assertEqual(len(invoice.era_agent_share_ids), 2)

        self.Engine.generate(self.date_from, self.date_to)
        self.assertAlmostEqual(self._lines(self.agent).commission_amount, 30.0)
        self.assertAlmostEqual(self._lines(second).commission_amount, 20.0)

    def test_credit_note_gives_the_commission_back(self):
        order = self._make_order([(self.product_fabric, 10, 100.0)])
        invoice = self._make_invoice(order)
        reversal = invoice._reverse_moves([{
            'invoice_date': self.order_date,
            'date': self.order_date,
        }])
        reversal.action_post()

        self.Engine.generate(self.date_from, self.date_to)
        lines = self._lines(self.agent)
        self.assertEqual(len(lines), 2)
        self.assertAlmostEqual(sum(lines.mapped('commission_amount')), 0.0)

    def test_generating_twice_does_not_double(self):
        order = self._make_order([(self.product_fabric, 10, 100.0)])
        self._make_invoice(order)
        self.Engine.generate(self.date_from, self.date_to)
        first = self._lines(self.agent)
        self.Engine.generate(self.date_from, self.date_to)
        second = self._lines(self.agent)
        self.assertEqual(first, second)
        self.assertAlmostEqual(sum(second.mapped('commission_amount')), 50.0)

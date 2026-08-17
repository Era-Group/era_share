from odoo import Command
from odoo.tests import tagged

from .common import CommissionCommon


@tagged('post_install', '-at_install')
class TestCommissionQuantity(CommissionCommon):
    """Commission counted by the unit: one line per product and per period."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.piece = cls.env['product.product'].create({
            'name': 'Packed Piece',
            'categ_id': cls.categ_accessory.id,
            'list_price': 10.0,
            'standard_price': 6.0,
            'taxes_id': [Command.clear()],
        })
        cls.rep = cls.env['era.commission.agent'].create({'name': 'Quantity Rep'})

    def _plan(self, name, basis='qty_sold', **values):
        plan = self.env['era.commission.plan'].create(dict({
            'name': name, 'basis': basis, 'rule_ids': [],
        }, **values))
        self._assign(self.rep, plan)
        return plan

    def _sell(self, quantity, date=None):
        order = self._make_order(
            [(self.piece, quantity, 10.0)], agent=self.rep, date=date)
        return self._make_invoice(order, date=date)

    # ------------------------------------------------------------------
    def test_case_four_net_units_times_the_unit_price(self):
        self._rate(self.rep, 'qty_sold', unit_price=0.5, product=self.piece)
        plan = self._plan('Case 4')
        plan.action_approve()

        invoice = self._sell(15000.0)
        self._make_refund(invoice, quantity=1000.0)

        self.Engine.generate(self.date_from, self.date_to, plans=plan)
        lines = self._lines(self.rep)
        # one line, not one per invoice line
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines.commission_type, 'qty_sold')
        self.assertAlmostEqual(lines.quantity, 14000.0)
        self.assertAlmostEqual(lines.unit_price, 0.5)
        self.assertAlmostEqual(lines.commission_amount, 7000.0)
        self.assertEqual(lines.date_from, self.date_from)
        self.assertEqual(lines.date_to, self.date_to)

    def test_more_returned_than_sold_earns_nothing(self):
        self._rate(self.rep, 'qty_sold', unit_price=0.5, product=self.piece)
        plan = self._plan('Negative net')
        plan.action_approve()

        invoice = self._sell(100.0)
        self._make_refund(invoice, quantity=250.0)

        self.Engine.generate(self.date_from, self.date_to, plans=plan)
        # a net of zero is no line at all, never a negative commission
        self.assertFalse(self._lines(self.rep))

    def test_collected_quantity_follows_what_was_paid(self):
        self._rate(self.rep, 'qty_collected', unit_price=0.5, product=self.piece)
        plan = self._plan('Case 4 collected', basis='qty_collected')
        plan.action_approve()

        invoice = self._sell(1000.0)          # 10 000 invoiced
        self._pay(invoice, 5000.0)            # half of it

        self.Engine.generate(self.date_from, self.date_to, plans=plan)
        line = self._lines(self.rep)
        self.assertAlmostEqual(line.quantity, 500.0)
        self.assertAlmostEqual(line.era_collection_ratio, 50.0)
        self.assertAlmostEqual(line.commission_amount, 250.0)

    def test_generating_twice_updates_the_same_line(self):
        self._rate(self.rep, 'qty_sold', unit_price=0.5, product=self.piece)
        plan = self._plan('Idempotent')
        plan.action_approve()
        self._sell(1000.0)

        self.Engine.generate(self.date_from, self.date_to, plans=plan)
        self.Engine.generate(self.date_from, self.date_to, plans=plan)
        lines = self._lines(self.rep)
        self.assertEqual(len(lines), 1)
        self.assertAlmostEqual(lines.commission_amount, 500.0)

    # --- the four sources of a unit price -------------------------------
    def test_a_tier_wins_over_everything(self):
        self.piece.commission_rate_per_unit = 0.1
        self._rate(self.rep, 'qty_sold', unit_price=0.5, product=self.piece)
        self._tier(self.piece, 0.0, 999.0, 0.2)
        self._tier(self.piece, 1000.0, 0.0, 0.8)
        plan = self._plan('Tiered')
        plan.action_approve()
        self._sell(2000.0)

        self.Engine.generate(self.date_from, self.date_to, plans=plan)
        line = self._lines(self.rep)
        self.assertAlmostEqual(line.unit_price, 0.8)
        self.assertAlmostEqual(line.commission_amount, 1600.0)

    def test_the_agent_price_wins_over_the_product(self):
        self.piece.commission_rate_per_unit = 0.1
        self._rate(self.rep, 'qty_sold', unit_price=0.5, product=self.piece)
        plan = self._plan('Agent price')
        plan.action_approve()
        self._sell(100.0)

        self.Engine.generate(self.date_from, self.date_to, plans=plan)
        self.assertAlmostEqual(self._lines(self.rep).unit_price, 0.5)

    def test_the_general_agent_price_wins_over_the_product(self):
        self.piece.commission_rate_per_unit = 0.1
        self._rate(self.rep, 'qty_sold', unit_price=0.3)
        plan = self._plan('General agent price')
        plan.action_approve()
        self._sell(100.0)

        self.Engine.generate(self.date_from, self.date_to, plans=plan)
        self.assertAlmostEqual(self._lines(self.rep).unit_price, 0.3)

    def test_the_product_price_is_the_last_resort(self):
        """No tier, nothing on the agent: the price written on the product."""
        self.piece.commission_rate_per_unit = 0.1
        # a tier on another product is what makes the plan approvable; it must
        # not leak onto this one
        self._tier(self.product_button, 0.0, 0.0, 9.0)
        plan = self._plan('Product price')
        plan.action_approve()
        self._sell(100.0)

        self.Engine.generate(self.date_from, self.date_to, plans=plan)
        line = self._lines(self.rep)
        self.assertAlmostEqual(line.unit_price, 0.1)
        self.assertAlmostEqual(line.commission_amount, 10.0)

    def test_an_officer_can_correct_the_unit_price(self):
        self._rate(self.rep, 'qty_sold', unit_price=0.5, product=self.piece)
        plan = self._plan('Correctable')
        plan.action_approve()
        self._sell(100.0)

        self.Engine.generate(self.date_from, self.date_to, plans=plan)
        line = self._lines(self.rep)
        self.assertAlmostEqual(line.commission_amount, 50.0)
        line.unit_price = 0.75
        self.assertAlmostEqual(line.commission_amount, 75.0)

    def test_a_quantity_target_is_taken_off_the_units(self):
        self._rate(self.rep, 'qty_sold', unit_price=0.5, product=self.piece)
        plan = self._plan('Quantity target', use_target=True, target_mode='deduct')
        plan.action_approve()
        self._sell(1000.0)
        self._target(self.rep, 'qty_sold', quantity=400.0, plan=plan)

        self.Engine.generate(self.date_from, self.date_to, plans=plan)
        line = self._lines(self.rep)
        self.assertTrue(line.use_target)
        self.assertAlmostEqual(line.target_qty, 400.0)
        self.assertAlmostEqual(line.commission_amount, 300.0)   # 600 x 0.5

    def test_no_tax_is_ever_taken_off_a_quantity_commission(self):
        self._rate(self.rep, 'qty_sold', unit_price=0.5, product=self.piece)
        plan = self._plan('No tax', deduct_tax=True)
        plan.action_approve()
        self._sell(100.0)

        self.Engine.generate(self.date_from, self.date_to, plans=plan)
        line = self._lines(self.rep)
        self.assertFalse(line.deduct_tax)
        self.assertAlmostEqual(line.tax_deducted, 0.0)
        self.assertAlmostEqual(line.commission_amount, 50.0)

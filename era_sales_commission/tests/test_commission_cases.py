from odoo import Command
from odoo.tests import tagged

from .common import CommissionCommon


@tagged('post_install', '-at_install')
class TestCommissionCases(CommissionCommon):
    """The three amount cases the business wrote down, with their own numbers.

    The plans here carry no rule at all: the percentage lives on the agent,
    which is the shape the business asked for. What is checked is the formula,
    end to end -- sales less returns less the target less the tax, times the
    agent's own rate.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tax = cls._sale_tax(15.0)
        cls.product = cls.env['product.product'].create({
            'name': 'Bulk Carton',
            'categ_id': cls.categ_accessory.id,
            'list_price': 1.0,
            'standard_price': 0.5,
            'taxes_id': [Command.set(cls.tax.ids)],
        })
        cls.rep_a = cls.env['era.commission.agent'].create({'name': 'Rep A'})
        cls.rep_b = cls.env['era.commission.agent'].create({'name': 'Rep B'})

    def _plan(self, name, basis, **values):
        plan = self.env['era.commission.plan'].create(dict({
            'name': name, 'basis': basis, 'rule_ids': [],
        }, **values))
        return plan

    def _sell(self, agent, amount, taxed=True, date=None):
        order = self._make_order(
            [(self.product, amount, 1.0)], agent=agent, date=date,
            taxes=self.tax if taxed else None)
        return self._make_invoice(order, date=date)

    # ------------------------------------------------------------------
    # case 1: sales less returns less target, tax off, agent rate
    # ------------------------------------------------------------------
    def test_case_one_sales_less_refund_less_target(self):
        self._rate(self.rep_a, 'sales', rate=2.0)
        plan = self._plan('Case 1', 'sales', use_target=True,
                          target_mode='deduct', deduct_tax=True,
                          tax_method='actual')
        self._assign(self.rep_a, plan)
        plan.action_approve()

        invoice = self._sell(self.rep_a, 100000.0)
        self._make_refund(invoice, quantity=10000.0)
        self._target(self.rep_a, 'sales', amount=50000.0, plan=plan)

        self.Engine.generate(self.date_from, self.date_to, plans=plan)
        lines = self._lines(self.rep_a)
        self.assertEqual(len(lines), 2)

        # base is collected with the tax in it, the tax is kept beside it
        self.assertAlmostEqual(sum(lines.mapped('base_amount')), 90000.0 * 1.15, 2)
        self.assertAlmostEqual(sum(lines.mapped('tax_deducted')), 90000.0 * 0.15, 2)
        self.assertAlmostEqual(sum(lines.mapped('target_amount')), 50000.0, 2)
        # (100000 - 10000 - 50000) x 2%
        self.assertAlmostEqual(sum(lines.mapped('commission_amount')), 800.0, 2)

    def test_case_one_without_deducting_the_tax(self):
        self._rate(self.rep_a, 'sales', rate=2.0)
        plan = self._plan('Case 1 gross', 'sales', deduct_tax=False)
        self._assign(self.rep_a, plan)
        plan.action_approve()

        self._sell(self.rep_a, 100000.0)
        self.Engine.generate(self.date_from, self.date_to, plans=plan)
        line = self._lines(self.rep_a)
        self.assertAlmostEqual(line.tax_deducted, 0.0)
        self.assertAlmostEqual(line.base_amount, 100000.0, 2)
        self.assertAlmostEqual(line.commission_amount, 2000.0, 2)

    def test_dividing_by_the_tax_rate_matches_the_actual_tax(self):
        """Both methods agree when the document really carries that rate."""
        self.company.era_commission_tax_rate = 15.0
        self._rate(self.rep_a, 'sales', rate=2.0)
        plan = self._plan('Case 1 divide', 'sales', deduct_tax=True,
                          tax_method='divide')
        self._assign(self.rep_a, plan)
        plan.action_approve()

        self._sell(self.rep_a, 100000.0)
        self.Engine.generate(self.date_from, self.date_to, plans=plan)
        line = self._lines(self.rep_a)
        self.assertAlmostEqual(line.base_amount, 115000.0, 2)
        self.assertAlmostEqual(line.tax_deducted, 15000.0, 2)
        self.assertAlmostEqual(line.commission_amount, 2000.0, 2)

    # ------------------------------------------------------------------
    # case 2: collection, two reps at two different rates, no target
    # ------------------------------------------------------------------
    def test_case_two_collection_at_each_rep_own_rate(self):
        self._rate(self.rep_a, 'collection', rate=2.0)
        self._rate(self.rep_b, 'collection', rate=2.5)
        plan = self._plan('Case 2', 'collection', deduct_tax=True,
                          tax_method='actual', use_target=False)
        self._assign(self.rep_a, plan)
        self._assign(self.rep_b, plan)
        plan.action_approve()

        for rep in (self.rep_a, self.rep_b):
            invoice = self._sell(rep, 100000.0)
            self._pay(invoice, 30000.0 * 1.15)

        self.Engine.generate(self.date_from, self.date_to, plans=plan)
        # 30000 net collected x each rate
        self.assertAlmostEqual(
            sum(self._lines(self.rep_a).mapped('commission_amount')), 600.0, 1)
        self.assertAlmostEqual(
            sum(self._lines(self.rep_b).mapped('commission_amount')), 750.0, 1)

    def test_the_agent_on_the_payment_is_the_one_credited(self):
        """A rep who collects someone else's invoice is paid for collecting it."""
        self._rate(self.rep_a, 'collection', rate=2.0)
        self._rate(self.rep_b, 'collection', rate=2.5)
        plan = self._plan('Case 2 crossed', 'collection', deduct_tax=False)
        self._assign(self.rep_a, plan)
        self._assign(self.rep_b, plan)
        plan.action_approve()

        invoice = self._sell(self.rep_a, 10000.0, taxed=False)
        self._pay(invoice, 10000.0, agent=self.rep_b)

        self.Engine.generate(self.date_from, self.date_to, plans=plan)
        self.assertFalse(self._lines(self.rep_a))
        self.assertAlmostEqual(
            sum(self._lines(self.rep_b).mapped('commission_amount')), 250.0, 2)

    # ------------------------------------------------------------------
    # case 3: collection less target
    # ------------------------------------------------------------------
    def test_case_three_collection_less_target(self):
        self._rate(self.rep_a, 'collection', rate=2.0)
        plan = self._plan('Case 3', 'collection', deduct_tax=True,
                          tax_method='actual', use_target=True,
                          target_mode='deduct')
        self._assign(self.rep_a, plan)
        plan.action_approve()

        invoice = self._sell(self.rep_a, 100000.0)
        self._pay(invoice, 100000.0 * 1.15)
        self._target(self.rep_a, 'collection', amount=50000.0, plan=plan)

        self.Engine.generate(self.date_from, self.date_to, plans=plan)
        lines = self._lines(self.rep_a)
        self.assertAlmostEqual(sum(lines.mapped('target_amount')), 50000.0, 2)
        # (100000 - 50000) x 2%
        self.assertAlmostEqual(
            sum(lines.mapped('commission_amount')), 1000.0, 1)

    # ------------------------------------------------------------------
    def test_an_officer_can_correct_the_rate_on_the_line(self):
        self._rate(self.rep_a, 'sales', rate=2.0)
        plan = self._plan('Correctable', 'sales', deduct_tax=False)
        self._assign(self.rep_a, plan)
        plan.action_approve()

        self._sell(self.rep_a, 1000.0, taxed=False)
        self.Engine.generate(self.date_from, self.date_to, plans=plan)
        line = self._lines(self.rep_a)
        self.assertAlmostEqual(line.commission_amount, 20.0)

        line.rate = 3.0
        self.assertAlmostEqual(line.commission_amount, 30.0)

    def test_a_plan_with_neither_rule_nor_rate_is_refused(self):
        from odoo.exceptions import UserError
        plan = self._plan('Empty', 'sales')
        self._assign(self.rep_b, plan)
        with self.assertRaises(UserError):
            plan.action_approve()

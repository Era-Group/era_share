from odoo import Command
from odoo.tests import tagged

from .common import CommissionCommon


@tagged('post_install', '-at_install')
class TestCommissionTarget(CommissionCommon):
    """A target is deducted from the base, or scales the period. Never both."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.plan.write({
            'use_target': True,
            'target_mode': 'factor',
            'target_tier_ids': [
                Command.create({'achievement_from': 0.0, 'factor': 0.0}),
                Command.create({'achievement_from': 60.0, 'factor': 50.0}),
                Command.create({'achievement_from': 80.0, 'factor': 80.0}),
                Command.create({'achievement_from': 100.0, 'factor': 100.0}),
                Command.create({'achievement_from': 120.0, 'factor': 110.0}),
            ],
        })

    def _plan_target(self, amount):
        return self._target(self.agent, 'sales', amount=amount, plan=self.plan)

    # --- the factor -----------------------------------------------------
    def test_seventy_percent_reached_gives_the_fifty_percent_tier(self):
        order = self._make_order([(self.product_fabric, 7, 100.0)])
        self._make_invoice(order)
        self.Engine.generate(self.date_from, self.date_to)

        target = self._plan_target(1000.0)
        self.assertAlmostEqual(target.achieved_amount, 700.0)
        self.assertAlmostEqual(target.achievement_rate, 70.0)
        self.assertAlmostEqual(target.factor, 50.0)

    def test_the_factor_scales_the_settlement(self):
        order = self._make_order([(self.product_fabric, 7, 100.0)])
        self._make_invoice(order)
        self._plan_target(1000.0)

        settlement = self.env['era.commission.settlement'].create({
            'agent_id': self.agent.id,
            'date_from': self.date_from,
            'date_to': self.date_to,
        })
        settlement.action_compute()

        self.assertAlmostEqual(settlement.amount_gross, 35.0)      # 700 x 5%
        self.assertAlmostEqual(settlement.amount_target_factor, 50.0)
        self.assertAlmostEqual(settlement.amount_target_adjust, -17.5)
        self.assertAlmostEqual(settlement.amount_total, 17.5)
        self.assertAlmostEqual(settlement.era_rep_total_commission, 17.5)

    def test_no_target_pays_in_full(self):
        order = self._make_order([(self.product_fabric, 7, 100.0)])
        self._make_invoice(order)
        settlement = self.env['era.commission.settlement'].create({
            'agent_id': self.agent.id,
            'date_from': self.date_from,
            'date_to': self.date_to,
        })
        settlement.action_compute()
        self.assertAlmostEqual(settlement.amount_target_factor, 100.0)
        self.assertAlmostEqual(settlement.amount_total, 35.0)

    def test_overachievement_pays_a_bonus(self):
        order = self._make_order([(self.product_fabric, 13, 100.0)])
        self._make_invoice(order)
        self.Engine.generate(self.date_from, self.date_to)
        target = self._plan_target(1000.0)
        self.assertAlmostEqual(target.achievement_rate, 130.0)
        self.assertAlmostEqual(target.factor, 110.0)

    def test_a_factor_plan_never_deducts_the_target_too(self):
        """The two ways of reading a target are exclusive by construction."""
        order = self._make_order([(self.product_fabric, 10, 100.0)])
        self._make_invoice(order)
        self._plan_target(500.0)
        self.Engine.generate(self.date_from, self.date_to)

        line = self._lines(self.agent)
        self.assertFalse(line.use_target)
        self.assertAlmostEqual(line.target_amount, 0.0)
        self.assertAlmostEqual(line.commission_amount, 50.0)   # 1000 x 5%

    # --- the deduction --------------------------------------------------
    def test_a_deducted_target_comes_off_the_base(self):
        self.plan.write({'target_mode': 'deduct'})
        order = self._make_order([(self.product_fabric, 10, 100.0)])
        self._make_invoice(order)
        self._plan_target(400.0)
        self.Engine.generate(self.date_from, self.date_to)

        line = self._lines(self.agent)
        self.assertTrue(line.use_target)
        self.assertAlmostEqual(line.target_amount, 400.0)
        # (1000 - 400) x 5%
        self.assertAlmostEqual(line.commission_amount, 30.0)

    def test_a_deducted_target_is_spread_over_the_lines(self):
        self.plan.write({'target_mode': 'deduct'})
        order = self._make_order([
            (self.product_fabric, 10, 100.0),    # 1000, 5%
            (self.product_button, 100, 10.0),    # 1000, 2%
        ])
        self._make_invoice(order)
        self._plan_target(1000.0)
        self.Engine.generate(self.date_from, self.date_to)

        lines = self._lines(self.agent)
        self.assertEqual(len(lines), 2)
        # half the target on each, since both carry the same base
        self.assertAlmostEqual(sum(lines.mapped('target_amount')), 1000.0)
        for line in lines:
            self.assertAlmostEqual(line.target_amount, 500.0)
        fabric = lines.filtered(lambda line: line.product_id == self.product_fabric)
        button = lines.filtered(lambda line: line.product_id == self.product_button)
        self.assertAlmostEqual(fabric.commission_amount, 25.0)   # 500 x 5%
        self.assertAlmostEqual(button.commission_amount, 10.0)   # 500 x 2%

    def test_a_line_below_its_share_of_the_target_earns_nothing(self):
        self.plan.write({'target_mode': 'deduct'})
        order = self._make_order([(self.product_fabric, 10, 100.0)])
        self._make_invoice(order)
        self._plan_target(5000.0)
        self.Engine.generate(self.date_from, self.date_to)

        line = self._lines(self.agent)
        self.assertAlmostEqual(line.commission_amount, 0.0)

    def test_removing_the_target_gives_the_base_back(self):
        self.plan.write({'target_mode': 'deduct'})
        order = self._make_order([(self.product_fabric, 10, 100.0)])
        self._make_invoice(order)
        target = self._plan_target(400.0)
        self.Engine.generate(self.date_from, self.date_to)
        self.assertAlmostEqual(self._lines(self.agent).commission_amount, 30.0)

        target.unlink()
        self.Engine.generate(self.date_from, self.date_to)
        line = self._lines(self.agent)
        self.assertFalse(line.use_target)
        self.assertAlmostEqual(line.commission_amount, 50.0)

    def test_two_overlapping_targets_are_refused(self):
        from odoo.exceptions import ValidationError
        self._plan_target(1000.0)
        with self.assertRaises(ValidationError):
            self._plan_target(2000.0)

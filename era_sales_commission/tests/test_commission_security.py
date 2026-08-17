from odoo.exceptions import AccessError
from odoo.tests import new_test_user, tagged

from .common import CommissionCommon


@tagged('post_install', '-at_install')
class TestCommissionSecurity(CommissionCommon):
    """An agent sees their own money, and signs nothing."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.agent_user = new_test_user(
            cls.env, login='era_commission_agent_user',
            groups='base.group_user,era_sales_commission.group_era_commission_agent')
        cls.agent.user_id = cls.agent_user

        cls.other_agent = cls.env['era.commission.agent'].create({
            'name': 'Someone Else'})
        cls._assign(cls.other_agent, cls.plan)

        order = cls._make_order([(cls.product_fabric, 10, 100.0)])
        cls._make_invoice(order)
        other_order = cls._make_order(
            [(cls.product_fabric, 10, 100.0)], agent=cls.other_agent)
        cls._make_invoice(other_order)
        cls.env['era.commission.engine'].generate(cls.date_from, cls.date_to)

    def test_an_agent_only_reads_their_own_lines(self):
        lines = self.env['era.commission.line'].with_user(self.agent_user).search([])
        self.assertTrue(lines)
        self.assertEqual(lines.agent_id, self.agent)

    def test_an_agent_cannot_write_a_commission_line(self):
        line = self.env['era.commission.line'].with_user(self.agent_user).search(
            [], limit=1)
        with self.assertRaises(AccessError):
            line.write({'rate': 999.0})

    def test_an_agent_cannot_approve_a_settlement(self):
        settlement = self.env['era.commission.settlement'].create({
            'agent_id': self.agent.id,
            'date_from': self.date_from,
            'date_to': self.date_to,
        })
        settlement.action_compute()
        with self.assertRaises(AccessError):
            settlement.with_user(self.agent_user).action_approve()

    def test_an_agent_only_reads_their_own_settlements(self):
        mine = self.env['era.commission.settlement'].create({
            'agent_id': self.agent.id,
            'date_from': self.date_from,
            'date_to': self.date_to,
        })
        theirs = self.env['era.commission.settlement'].create({
            'agent_id': self.other_agent.id,
            'date_from': self.date_from,
            'date_to': self.date_to,
        })
        visible = self.env['era.commission.settlement'].with_user(
            self.agent_user).search([])
        self.assertIn(mine, visible)
        self.assertNotIn(theirs, visible)

    def test_an_officer_sees_everyone(self):
        officer = new_test_user(
            self.env, login='era_commission_officer_user',
            groups='base.group_user,era_sales_commission.group_era_commission_officer')
        lines = self.env['era.commission.line'].with_user(officer).search([])
        self.assertEqual(lines.agent_id, self.agent + self.manager + self.other_agent)

from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import CommissionCommon


@tagged('post_install', '-at_install')
class TestCommissionPayroll(CommissionCommon):
    """Pushing a statement onto a payslip: once, by hand, and never silently."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.employee = cls._employee('Field Rep')
        cls.agent.employee_id = cls.employee
        cls.input_type = cls.env.ref(
            'era_sales_commission.era_commission_input_type')

    def _approved_settlement(self, agent=None):
        order = self._make_order([(self.product_fabric, 10, 100.0)],
                                 agent=agent or self.agent)
        self._make_invoice(order)
        settlement = self.env['era.commission.settlement'].create({
            'agent_id': (agent or self.agent).id,
            'date_from': self.date_from,
            'date_to': self.date_to,
        })
        settlement.action_compute()
        settlement.action_approve()
        return settlement

    def test_the_input_type_and_the_rule_are_installed(self):
        self.assertEqual(self.input_type.code, 'EXT_COMM')
        rule = self.env.ref('era_sales_commission.era_rule_external_commission')
        self.assertEqual(rule.amount_select, 'input')
        self.assertEqual(rule.amount_other_input_id, self.input_type)
        self.assertIn(
            self.input_type,
            self.env.ref('hr_payroll.structure_002').input_line_type_ids)

    def test_pushing_writes_one_input_of_the_net_amount(self):
        settlement = self._approved_settlement()
        payslip = self._payslip(self.employee)
        settlement.action_push_to_payslip()

        inputs = payslip.input_line_ids.filtered(
            lambda line: line.input_type_id == self.input_type)
        self.assertEqual(len(inputs), 1)
        self.assertAlmostEqual(
            inputs.amount, settlement.era_rep_total_commission)
        self.assertAlmostEqual(inputs.amount, 50.0)
        self.assertTrue(settlement.era_payslip_pushed)
        self.assertEqual(settlement.era_payslip_id, payslip)

    def test_pushing_twice_is_refused(self):
        settlement = self._approved_settlement()
        self._payslip(self.employee)
        settlement.action_push_to_payslip()
        with self.assertRaises(UserError):
            settlement.action_push_to_payslip()

    def test_an_agent_without_an_employee_cannot_be_pushed(self):
        stranger = self.env['era.commission.agent'].create({'name': 'No HR Rep'})
        self._assign(stranger, self.plan)
        settlement = self._approved_settlement(agent=stranger)
        self._payslip(self.employee)
        with self.assertRaises(UserError):
            settlement.action_push_to_payslip()

    def test_without_an_open_payslip_the_error_says_so(self):
        settlement = self._approved_settlement()
        with self.assertRaises(UserError):
            settlement.action_push_to_payslip()

    def test_a_draft_settlement_is_not_pushed(self):
        order = self._make_order([(self.product_fabric, 10, 100.0)])
        self._make_invoice(order)
        settlement = self.env['era.commission.settlement'].create({
            'agent_id': self.agent.id,
            'date_from': self.date_from,
            'date_to': self.date_to,
        })
        settlement.action_compute()
        self._payslip(self.employee)
        with self.assertRaises(UserError):
            settlement.action_push_to_payslip()

    def test_a_pushed_settlement_cannot_be_cancelled(self):
        settlement = self._approved_settlement()
        self._payslip(self.employee)
        settlement.action_push_to_payslip()
        with self.assertRaises(UserError):
            settlement.action_cancel()

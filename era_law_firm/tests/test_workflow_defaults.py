"""Every required field with no default is a question the form asks mid-task.

Some are real — a case needs a client. Some are the software asking for its
own bookkeeping: which stage a new case starts at, which product an engagement
bills through, when a hearing ends. Those should be answered before the lawyer
is asked.
"""
from datetime import timedelta

from odoo import fields
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestWorkflowDefaults(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.client = cls.env['res.partner'].create({'name': 'موكّل'})
        cls.lawyer = cls.env['res.users'].create({
            'name': 'محامي القضية', 'login': 'case_lawyer_defaults'})
        cls.case = cls.env['legal.case'].create({
            'name': 'قضية', 'client_id': cls.client.id, 'lawyer_id': cls.lawyer.id,
            'case_type': 'litigation', 'company_id': cls.env.company.id,
            'stage_id': cls.env.ref('era_law_firm.stage_intake').id})

    def test_a_new_case_starts_at_the_first_stage(self):
        stage_id = self.env['legal.case'].default_get(['stage_id']).get('stage_id')
        first = self.env['legal.case.stage'].search([], order='sequence, id', limit=1)
        self.assertEqual(stage_id, first.id, 'every case starts at the beginning')

    def test_an_engagement_bills_through_a_shipped_product(self):
        """A lawyer has no reason to hold an opinion about which product."""
        product_id = self.env['legal.engagement'].default_get(['product_id']).get('product_id')
        self.assertTrue(product_id)
        self.assertEqual(self.env['product.product'].browse(product_id).type, 'service')

    def test_an_expense_inherits_the_only_active_engagement(self):
        """One active engagement is not a choice; it is the answer."""
        engagement = self.env['legal.engagement'].create({
            'case_id': self.case.id, 'name': 'أتعاب', 'billing_type': 'hourly',
            'hourly_rate': 400})
        engagement.action_activate()
        expense = self.env['legal.expense'].new({'case_id': self.case.id})
        expense._onchange_case_fills_engagement()
        self.assertEqual(expense.engagement_id, engagement)

    def test_two_engagements_leave_the_choice_alone(self):
        for name in ('أتعاب أ', 'أتعاب ب'):
            self.env['legal.engagement'].create({
                'case_id': self.case.id, 'name': name, 'billing_type': 'hourly',
                'hourly_rate': 400}).action_activate()
        expense = self.env['legal.expense'].new({'case_id': self.case.id})
        expense._onchange_case_fills_engagement()
        self.assertFalse(expense.engagement_id,
                         'guessing between two is worse than asking')

    def test_a_hearing_ends_an_hour_after_it_starts(self):
        """A lawyer knows when a hearing is called, rarely when it will end."""
        hearing = self.env['legal.hearing'].new({
            'start_datetime': fields.Datetime.now()})
        hearing._onchange_start_sets_end()
        self.assertEqual(hearing.stop_datetime,
                         hearing.start_datetime + timedelta(minutes=60))

    def test_a_hearing_takes_the_case_lawyer_and_a_name(self):
        hearing = self.env['legal.hearing'].new({'case_id': self.case.id})
        hearing._onchange_case_fills_hearing()
        self.assertEqual(hearing.lawyer_id, self.lawyer)
        self.assertIn(self.case.display_name, hearing.name)

    def test_a_deadline_belongs_to_the_case_lawyer(self):
        deadline = self.env['legal.deadline'].new({'case_id': self.case.id})
        deadline._onchange_case_fills_owner()
        self.assertEqual(deadline.user_id, self.lawyer)

    def test_a_deadline_says_where_it_came_from(self):
        values = self.env['legal.deadline'].default_get(['user_id', 'source'])
        self.assertTrue(values.get('source'), 'a required Char with no default is a riddle')

    def test_the_whole_chain_needs_no_bookkeeping_answers(self):
        """Create everything using only what a lawyer actually knows."""
        engagement = self.env['legal.engagement'].create(dict(
            self.env['legal.engagement'].default_get(['product_id']),
            case_id=self.case.id, name='أتعاب', billing_type='hourly',
            hourly_rate=500))
        engagement.action_activate()
        expense = self.env['legal.expense'].new({'case_id': self.case.id})
        expense._onchange_case_fills_engagement()
        self.env['legal.expense'].create(dict(
            self.env['legal.expense'].default_get(['product_id']),
            name='رسوم', case_id=self.case.id,
            engagement_id=expense.engagement_id.id, amount=100))
        self.assertTrue(engagement.product_id, 'nothing had to be looked up')

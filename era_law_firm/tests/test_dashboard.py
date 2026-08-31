"""The landing page tells each role the truth about its own work.

Two invariants matter more than any pixel. The number on a tile and the list
its click opens must come from the same domain — a counter that says 3 while
the list shows 5 teaches people to ignore both. And each role sees its own
sections: the supervisor's team numbers do not render for a lawyer, and the
lawyer's numbers are scoped to them.
"""
from odoo import fields
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestDashboard(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        def user(name, login, groups):
            return cls.env['res.users'].create({
                'name': name, 'login': login,
                'group_ids': [(6, 0, [cls.env.ref(g).id for g in groups])]})
        cls.lawyer = user('محامي اللوحة', 'dash_lawyer',
                          ['base.group_user', 'era_law_firm.group_legal_lawyer'])
        cls.colleague = user('محامٍ زميل', 'dash_colleague',
                             ['base.group_user', 'era_law_firm.group_legal_lawyer'])
        cls.supervisor = user('مشرف اللوحة', 'dash_supervisor',
                              ['base.group_user', 'era_law_firm.group_legal_supervisor'])
        cls.client = cls.env['res.partner'].create({'name': 'موكّل اللوحة'})

    def _dashboard(self, user):
        Dashboard = self.env['legal.dashboard'].with_user(user)
        return Dashboard.new(Dashboard.default_get(list(Dashboard._fields)))

    def _case_for(self, lawyer):
        wizard = self.env['legal.intake.wizard'].with_user(lawyer).create({
            'client_id': self.client.id, 'case_type': 'litigation',
            'lawyer_id': lawyer.id, 'engagement_type': 'none'})
        return self.env['legal.case'].browse(wizard.action_open_case()['res_id'])

    def test_sections_follow_the_role(self):
        lawyer_board = self._dashboard(self.lawyer)
        self.assertTrue(lawyer_board.show_lawyer)
        self.assertFalse(lawyer_board.show_supervisor,
                         "a lawyer does not get the team's numbers")
        supervisor_board = self._dashboard(self.supervisor)
        self.assertTrue(supervisor_board.show_supervisor)
        self.assertTrue(supervisor_board.show_lawyer,
                        'the implication chain: a supervisor is also a lawyer')

    def test_my_numbers_are_mine(self):
        self._case_for(self.lawyer)
        self._case_for(self.colleague)
        board = self._dashboard(self.lawyer)
        self.assertEqual(board.my_open_cases, 1,
                         "the colleague's case is not my work")

    def test_the_number_and_the_list_agree(self):
        """The tile's count and its button read one domain; prove it holds."""
        case = self._case_for(self.lawyer)
        Dashboard = self.env['legal.dashboard'].with_user(self.lawyer)
        board = Dashboard.create({})
        action = board.action_my_open_cases()
        listed = self.env['legal.case'].with_user(self.lawyer).search(action['domain'])
        self.assertIn(case, listed)
        self.assertEqual(len(listed), self._dashboard(self.lawyer).my_open_cases)

    def test_overdue_deadlines_surface_in_red(self):
        case = self._case_for(self.lawyer)
        self.env['legal.deadline'].create({
            'name': 'فات موعدها', 'case_id': case.id, 'user_id': self.lawyer.id,
            'deadline_date': fields.Date.today() - fields.date_utils.relativedelta(days=3),
            'source': 'يدوي', 'state': 'confirmed', 'company_id': self.env.company.id})
        board = self._dashboard(self.lawyer)
        self.assertEqual(board.my_deadlines_overdue, 1)
        self.assertEqual(board.my_deadlines_week, 0,
                         'overdue and upcoming are different urgencies')

    def test_blocked_conflicts_reach_the_supervisor(self):
        opponent = self.env['res.partner'].create({'name': 'خصم مكرر'})
        first = self._case_for(self.lawyer)
        self.env['legal.case.party'].create({
            'case_id': first.id, 'partner_id': opponent.id, 'role': 'opponent',
            'company_id': self.env.company.id})
        # a second case against the same opponent for another client → blocked
        wizard = self.env['legal.intake.wizard'].with_user(self.colleague).create({
            'client_id': self.env['res.partner'].create({'name': 'موكّل ثانٍ'}).id,
            'case_type': 'litigation', 'lawyer_id': self.colleague.id,
            'engagement_type': 'none', 'opponent_ids': [(6, 0, opponent.ids)]})
        blocked_case = self.env['legal.case'].browse(wizard.action_open_case()['res_id'])
        self.assertEqual(blocked_case.conflict_check_id.state, 'blocked', 'precondition')
        board = self._dashboard(self.supervisor)
        self.assertGreaterEqual(board.blocked_conflicts, 1)
        action = self.env['legal.dashboard'].with_user(self.supervisor).create(
            {}).action_blocked_conflicts()
        found = self.env['legal.conflict.check'].with_user(self.supervisor).search(
            action['domain'])
        self.assertIn(blocked_case.conflict_check_id, found)

    def test_the_dashboard_is_the_first_thing_the_app_opens(self):
        root = self.env.ref('era_law_firm.menu_legal_root')
        children = self.env['ir.ui.menu'].search(
            [('parent_id', '=', root.id)], order='sequence, id')
        self.assertEqual(children[0], self.env.ref('era_law_firm.menu_legal_dashboard'))

    def test_every_button_answers_for_every_role(self):
        """No tile may crash for a role that can see it."""
        for user in (self.lawyer, self.supervisor):
            board = self.env['legal.dashboard'].with_user(user).create({})
            for name in ('action_my_open_cases', 'action_my_hearings_week',
                         'action_my_deadlines_overdue', 'action_my_deadlines_week',
                         'action_my_draft_cases', 'action_my_draft_time',
                         'action_open_intake'):
                action = getattr(board, name)()
                self.assertTrue(action.get('res_model') or action.get('type'), name)

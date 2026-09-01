"""The landing page tells each role the truth about its own work.

Two invariants matter more than any pixel. The number on a tile and the list
its click opens must come from the same domain — a counter that says 3 while
the list shows 5 teaches people to ignore both. And each role sees its own
sections: the supervisor's team numbers do not render for a lawyer, and the
lawyer's numbers are scoped to them.
"""
from ast import literal_eval
from datetime import timedelta

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
            'deadline_date': fields.Date.today() - timedelta(days=3),
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

    def test_the_app_menu_is_sections_not_a_wall(self):
        """Thirteen entries in one column is a list to read every time."""
        root = self.env.ref('era_law_firm.menu_legal_root')
        children = self.env['ir.ui.menu'].sudo().search([('parent_id', '=', root.id)])
        self.assertLessEqual(len(children), 8, [c.name for c in children])
        # and nothing was orphaned on the way
        for xmlid in ('menu_legal_cases', 'menu_legal_hearings', 'menu_legal_deadlines',
                      'menu_legal_documents', 'menu_legal_consultations',
                      'menu_legal_conflict_check', 'menu_legal_trust',
                      'menu_legal_time', 'menu_legal_config_courts'):
            menu = self.env.ref('era_law_firm.%s' % xmlid)
            self.assertTrue(menu.parent_id, xmlid)
            self.assertEqual(menu.parent_path.split('/')[0], str(root.id), xmlid)

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


@tagged('post_install', '-at_install')
class TestConflictRegister(TransactionCase):
    """The register has to say which case, whose client, and how certain."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.lawyer = cls.env['res.users'].create({
            'name': 'محامي السجل', 'login': 'reg_lawyer',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id,
                                  cls.env.ref('era_law_firm.group_legal_lawyer').id])]})
        cls.client = cls.env['res.partner'].create({'name': 'موكّل السجل'})

    def _case(self, client=None, opponents=None):
        wizard = self.env['legal.intake.wizard'].with_user(self.lawyer).create({
            'client_id': (client or self.client).id, 'case_type': 'litigation',
            'lawyer_id': self.lawyer.id, 'engagement_type': 'none',
            'opponent_ids': [(6, 0, (opponents or self.env['res.partner']).ids)]})
        return self.env['legal.case'].browse(wizard.action_open_case()['res_id'])

    def test_a_row_names_its_case_and_client(self):
        check = self._case().conflict_check_id
        self.assertEqual(check.client_id, self.client)
        self.assertEqual(check.lawyer_id, self.lawyer)
        self.assertIn(check.case_id.name, check.display_name,
                      'a bare id tells a supervisor nothing')

    def test_the_run_stamps_its_time(self):
        check = self._case().conflict_check_id
        self.assertTrue(check.checked_on, 'the register sorts on recency')

    def test_the_strongest_evidence_wins_the_summary(self):
        opponent = self.env['res.partner'].create({
            'name': 'خصم مشترك', 'legal_identity_number': '1122334455'})
        first = self._case(opponents=opponent)
        first.action_confirm()
        twin = self.env['res.partner'].create({
            'name': 'خصم مشترك', 'legal_identity_number': '1122334455'})
        second = self._case(
            client=self.env['res.partner'].create({'name': 'موكّل آخر'}),
            opponents=twin)
        check = second.conflict_check_id
        self.assertEqual(check.state, 'blocked')
        self.assertGreaterEqual(check.match_count, 1)
        self.assertIn(check.strongest_basis, ('same_partner', 'identity_number'),
                      'a shared ID is stronger evidence than a name coincidence')

    def test_the_register_action_opens_on_what_is_blocked(self):
        action = self.env['ir.actions.act_window']._for_xml_id(
            'era_law_firm.action_legal_conflict_check')
        # act_window.context is a Char: what comes back is source, not a dict.
        context = literal_eval(action['context'])
        self.assertEqual(context.get('search_default_blocked'), 1)


@tagged('post_install', '-at_install')
class TestDashboardScope(TransactionCase):
    """Whose work the dashboard counts, and who gets to choose.

    An administrator whose name was on one case read "1" beside a label that
    said Open Cases while the firm had forty-five. Someone running a practice
    should land on the practice; a lawyer has nothing to choose, because the
    record rules only ever show them their own.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.supervisor = cls.env['res.users'].create({
            'name': 'مشرف النطاق', 'login': 'scope_supervisor',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id,
                                  cls.env.ref('era_law_firm.group_legal_supervisor').id])]})
        cls.colleague = cls.env['res.users'].create({
            'name': 'زميل النطاق', 'login': 'scope_colleague',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id,
                                  cls.env.ref('era_law_firm.group_legal_lawyer').id])]})
        client = cls.env['res.partner'].create({'name': 'موكّل النطاق'})
        for lawyer in (cls.supervisor, cls.colleague, cls.colleague):
            wizard = cls.env['legal.intake.wizard'].with_user(lawyer).create({
                'client_id': client.id, 'case_type': 'litigation',
                'lawyer_id': lawyer.id, 'engagement_type': 'none'})
            wizard.action_open_case()

    def _board(self, user, scope=None):
        Dashboard = self.env['legal.dashboard'].with_user(user)
        board = Dashboard.new(Dashboard.default_get(list(Dashboard._fields)))
        if scope:
            board.scope = scope
        return board

    def test_a_supervisor_lands_on_the_whole_firm(self):
        board = self._board(self.supervisor)
        self.assertEqual(board.scope, 'firm')
        firm = self.env['legal.case'].sudo().search_count([('state', '=', 'confirmed')])
        self.assertEqual(board.my_open_cases, firm)
        self.assertGreater(firm, 1, 'precondition: colleagues hold cases too')

    def test_narrowing_to_mine_reprices_the_page(self):
        board = self._board(self.supervisor, scope='mine')
        self.assertEqual(board.my_open_cases, 1, 'the supervisor holds one file')

    def test_a_lawyer_lands_on_their_own_and_cannot_widen(self):
        board = self._board(self.colleague)
        self.assertEqual(board.scope, 'mine')
        self.assertEqual(board.my_open_cases, 2)
        board.scope = 'firm'
        self.assertEqual(board.my_open_cases, 2,
                         'widening belongs to a supervisor, not to a field value')

    def test_the_number_and_the_list_agree_in_both_scopes(self):
        for scope, user in (('firm', self.supervisor), ('mine', self.supervisor)):
            board = self.env['legal.dashboard'].with_user(user).create({'scope': scope})
            action = board.action_my_open_cases()
            listed = self.env['legal.case'].with_user(user).search(action['domain'])
            self.assertEqual(len(listed), board.my_open_cases, scope)

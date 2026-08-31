"""What a client may see of their own file — enforced, not assumed.

The portal previously had no rules at all: its own list page answered 403,
and the detail page compensated with sudo, meaning whatever a template ever
rendered would have leaked. These tests pin the record-rule boundary itself,
as the portal user, so a template author can no longer widen exposure by
accident: the rule refuses before the template matters.
"""
from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestPortalAccess(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.client_partner = cls.env['res.partner'].create({'name': 'موكّل البوابة'})
        cls.portal_user = cls.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'موكّل البوابة', 'login': 'portal_rules_walk',
            'partner_id': cls.client_partner.id,
            'group_ids': [(6, 0, [cls.env.ref('base.group_portal').id])]})
        cls.stranger = cls.env['res.partner'].create({'name': 'موكّل آخر'})
        cls.case = cls._make_case(cls, cls.client_partner)
        cls.foreign_case = cls._make_case(cls, cls.stranger)

    def _make_case(self, partner):
        """The wizard confirms when the check is clear; assert it, don't hope."""
        wizard = self.env['legal.intake.wizard'].create({
            'client_id': partner.id, 'case_type': 'litigation',
            'lawyer_id': self.env.user.id, 'engagement_type': 'none'})
        case = self.env['legal.case'].browse(wizard.action_open_case()['res_id'])
        assert case.state == 'confirmed', 'fixture expects a clear check'
        return case

    def _as_portal(self, model):
        return self.env[model].with_user(self.portal_user)

    # ------------------------------------------------------------- cases
    def test_the_client_sees_their_own_cases_and_no_others(self):
        cases = self._as_portal('legal.case').search([])
        self.assertIn(self.case, cases)
        self.assertNotIn(self.foreign_case, cases)

    def test_reading_a_foreign_case_is_refused(self):
        with self.assertRaises(AccessError):
            self._as_portal('legal.case').browse(self.foreign_case.id).read(['name'])

    def test_a_draft_case_is_the_firm_still_deciding(self):
        draft = self.env['legal.case'].create({
            'name': 'مسودة', 'client_id': self.client_partner.id,
            'lawyer_id': self.env.user.id, 'case_type': 'litigation',
            'company_id': self.company.id,
            'stage_id': self.env.ref('era_law_firm.stage_intake').id})
        self.assertNotIn(draft, self._as_portal('legal.case').search([]))

    def test_the_portal_is_a_window_not_a_pen(self):
        with self.assertRaises(AccessError):
            self._as_portal('legal.case').browse(self.case.id).write({'name': 'X'})

    # -------------------------------------------------------- documents
    def _document(self, published, state):
        attachment = self.env['ir.attachment'].create({
            'name': 'م.txt', 'raw': b'doc', 'mimetype': 'text/plain'})
        return self.env['legal.document'].create({
            'name': 'مستند', 'case_id': self.case.id,
            'attachment_id': attachment.id,
            'portal_published': published, 'state': state})

    def test_only_published_and_approved_documents_exist_for_the_client(self):
        visible = self._document(True, 'approved')
        self._document(True, 'draft')       # published then sent back to draft
        self._document(False, 'approved')   # approved but never shared
        found = self._as_portal('legal.document').search([])
        self.assertEqual(found, visible,
                         'publish-time approval is not enough; the rule re-checks '
                         'both conditions on every read')

    # ---------------------------------------------------------- hearings
    def test_a_tentative_hearing_is_the_firms_planning(self):
        from datetime import timedelta
        now = fields.Datetime.now()
        later = now + timedelta(hours=1)
        confirmed = self.env['legal.hearing'].create({
            'name': 'جلسة مؤكدة', 'case_id': self.case.id,
            'lawyer_id': self.env.user.id, 'state': 'confirmed',
            'start_datetime': now, 'stop_datetime': later,
            'company_id': self.company.id})
        self.env['legal.hearing'].create({
            'name': 'جلسة مبدئية', 'case_id': self.case.id,
            'lawyer_id': self.env.user.id,
            'start_datetime': now, 'stop_datetime': later,
            'company_id': self.company.id})
        found = self._as_portal('legal.hearing').search([])
        self.assertEqual(found, confirmed)

    # ----------------------------------------------------------- parties
    def test_a_party_shows_only_when_a_lawyer_decided_it_should(self):
        shown = self.env['legal.case.party'].create({
            'case_id': self.case.id, 'role': 'opponent', 'portal_visible': True,
            'partner_id': self.env['res.partner'].create({'name': 'خصم ظاهر'}).id,
            'company_id': self.company.id})
        self.env['legal.case.party'].create({
            'case_id': self.case.id, 'role': 'opponent',
            'partner_id': self.env['res.partner'].create({'name': 'خصم مستور'}).id,
            'company_id': self.company.id})
        found = self._as_portal('legal.case.party').search([])
        self.assertEqual(found, shown, 'portal_visible defaults to off, and off means off')

    # ------------------------------------------------------------- trust
    def test_the_client_sees_their_own_money_and_only_posted_movements(self):
        account = self.env['legal.trust.account'].create({
            'partner_id': self.client_partner.id, 'company_id': self.company.id})
        posted = self.env['legal.trust.transaction'].create({
            'trust_account_id': account.id, 'transaction_type': 'deposit',
            'amount': 5000, 'case_id': self.case.id})
        posted.action_post()
        self.env['legal.trust.transaction'].create({
            'trust_account_id': account.id, 'transaction_type': 'deposit',
            'amount': 700, 'case_id': self.case.id})  # left in draft
        self.assertIn(account, self._as_portal('legal.trust.account').search([]))
        self.assertEqual(self._as_portal('legal.trust.transaction').search([]), posted)

    def test_anothers_trust_account_does_not_exist_for_them(self):
        foreign = self.env['legal.trust.account'].create({
            'partner_id': self.stranger.id, 'company_id': self.company.id})
        self.assertNotIn(foreign, self._as_portal('legal.trust.account').search([]))

    def test_a_cancelled_case_disappears_from_the_client(self):
        """Cancelled is an engagement that never happened."""
        self.case.sudo().write({'state': 'cancelled'})
        self.assertNotIn(self.case, self._as_portal('legal.case').search([]))

    def test_firm_internals_are_not_on_the_orm_surface(self):
        """Rules scope rows; only field groups can hide a column. A portal
        session can call search_read directly, so the template showing little
        proves nothing — the field itself has to refuse."""
        from odoo.exceptions import AccessError
        rows = self._as_portal('legal.case').search_read([], ['name'])
        self.assertTrue(rows, 'precondition: the client does see their case')
        for field in ('outcome', 'billable_hours', 'expense_amount', 'team_user_ids'):
            with self.assertRaises(AccessError, msg=field):
                self._as_portal('legal.case').search_read([], [field])

    def test_a_refunds_staff_reason_is_not_readable(self):
        account = self.env['legal.trust.account'].create({
            'partner_id': self.client_partner.id, 'company_id': self.company.id})
        self.env['legal.trust.transaction'].create({
            'trust_account_id': account.id, 'transaction_type': 'deposit',
            'amount': 900, 'case_id': self.case.id}).action_post()
        from odoo.exceptions import AccessError
        with self.assertRaises(AccessError):
            self._as_portal('legal.trust.transaction').search_read([], ['reason'])

"""The demo data has to make the portal demonstrable, both ways.

Whoever tests the portal grabs the first demo email they find, and the
plausible-looking ones are the opponents — which produced a bug report that
was actually the security boundary working. Ship the two logins with the
data: one that sees a full file, one that correctly sees nothing.
"""
from odoo.tests.common import TransactionCase, tagged

from odoo.addons.era_law_firm.models.legal_demo import (
    PORTAL_CLIENT_LOGIN, PORTAL_OPPONENT_LOGIN)


@tagged('post_install', '-at_install')
class TestDemoPortalAccounts(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.cases = cls.env['legal.demo.data']._generate(6)

    def _user(self, login):
        return self.env['res.users'].sudo().search([('login', '=', login)], limit=1)

    def test_the_client_login_exists_and_sees_a_full_file(self):
        user = self._user(PORTAL_CLIENT_LOGIN)
        self.assertTrue(user, 'the load screen promises this login')
        self.assertTrue(user.has_group('base.group_portal'))
        visible = self.env['legal.case'].with_user(user).search([])
        self.assertTrue(visible, 'a demo client account with nothing to show '
                                 'demonstrates nothing')
        self.assertTrue(all(c.client_id.commercial_partner_id
                            == user.partner_id.commercial_partner_id for c in visible))

    def test_the_opponent_login_exists_and_correctly_sees_nothing(self):
        user = self._user(PORTAL_OPPONENT_LOGIN)
        if not user:
            self.skipTest('this batch produced no pure opponent')
        self.assertFalse(self.env['legal.case'].with_user(user).search([]),
                         'the other side of a case must never see the file')

    def test_the_opponent_account_is_never_also_a_client(self):
        """Otherwise the demonstration of the boundary demonstrates a breach."""
        user = self._user(PORTAL_OPPONENT_LOGIN)
        if not user:
            self.skipTest('this batch produced no pure opponent')
        self.assertFalse(self.env['legal.case'].sudo().search_count([
            ('client_id.commercial_partner_id', '=',
             user.partner_id.commercial_partner_id.id)]))

    def test_purge_takes_the_logins_with_it(self):
        self.env['legal.demo.data']._purge()
        self.assertFalse(self._user(PORTAL_CLIENT_LOGIN))
        self.assertFalse(self._user(PORTAL_OPPONENT_LOGIN))

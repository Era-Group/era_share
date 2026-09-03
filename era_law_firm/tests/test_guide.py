"""The manual has to be true, reachable, and the firm's to extend.

A guide is the one part of a product that rots silently: nothing fails when it
describes a menu that moved or a button that was renamed. These tests hold the
few claims that can be checked mechanically, and leave the prose to review.
"""
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestUserGuide(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Topic = cls.env['legal.guide.topic'].sudo()
        cls.shipped = cls.Topic.search([('is_shipped', '=', True)])

    def test_the_guide_ships_with_content(self):
        self.assertGreaterEqual(len(self.shipped), 10)
        for topic in self.shipped:
            self.assertTrue(topic.summary, topic.name)
            self.assertGreater(len(topic.body or ''), 400,
                               '%s is a heading, not a topic' % topic.name)

    def test_every_topic_reads_in_arabic(self):
        """The firms using this read Arabic; an untranslated topic is unusable."""
        for topic in self.shipped.with_context(lang='ar_001'):
            self.assertTrue(any('؀' <= c <= 'ۿ' for c in topic.name),
                            'untranslated title: %s' % topic.name)
            self.assertTrue(any('؀' <= c <= 'ۿ' for c in (topic.body or '')),
                            'untranslated body: %s' % topic.name)

    def test_it_teaches_the_menus_that_exist(self):
        """Every section the guide names must be a menu somebody can click."""
        for xmlid in ('menu_legal_matters', 'menu_legal_schedule',
                      'menu_legal_documents_root', 'menu_legal_billing',
                      'menu_legal_config', 'menu_legal_dashboard',
                      'menu_legal_conflict_check', 'menu_legal_guide'):
            self.assertTrue(self.env.ref('era_law_firm.%s' % xmlid, False), xmlid)

    def test_it_promises_only_states_the_models_have(self):
        document_states = dict(self.env['legal.document']._fields['state'].selection)
        for state in ('draft', 'review', 'approved', 'rejected', 'archived'):
            self.assertIn(state, document_states)
        transaction_types = dict(
            self.env['legal.trust.transaction']._fields['transaction_type'].selection)
        for kind in ('deposit', 'refund', 'transfer'):
            self.assertIn(kind, transaction_types)

    def test_staff_read_it_and_a_manager_extends_it(self):
        staff = self.env['res.users'].create({
            'name': 'قارئ الدليل', 'login': 'guide_reader',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id,
                                  self.env.ref('era_law_firm.group_legal_lawyer').id])]})
        self.assertTrue(self.env['legal.guide.topic'].with_user(staff).search_count([]))
        from odoo.exceptions import AccessError
        with self.assertRaises(AccessError):
            self.env['legal.guide.topic'].with_user(staff).create(
                {'name': 'قاعدة بيتية', 'category': 'daily'})

        manager = self.env['res.users'].create({
            'name': 'مدير الدليل', 'login': 'guide_manager',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id,
                                  self.env.ref('era_law_firm.group_legal_manager').id])]})
        house_rule = self.env['legal.guide.topic'].with_user(manager).create(
            {'name': 'قاعدة المكتب', 'category': 'daily', 'body': '<p>نص</p>'})
        self.assertFalse(house_rule.is_shipped,
                         "a firm's own topic must not look shipped")

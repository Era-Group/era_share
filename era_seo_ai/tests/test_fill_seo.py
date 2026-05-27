"""Tests for the proactive "AI: Fill SEO" action on era.seo.mixin."""
from unittest.mock import MagicMock, patch

from odoo.tests import TransactionCase, tagged

from odoo.addons.era_seo_ai.models.ai_client import AIClient


def _mock_agent(reply_json):
    agent = MagicMock()
    agent.name = 'Test SEO Agent'
    agent.llm_model = 'gpt-test'
    agent.get_direct_response.return_value = [reply_json]
    return agent


_FULL_REPLY = (
    '{"seo_title": "Cloud Accounting for Saudi SMEs | ERA", '
    '"seo_description": "ZATCA-ready, VAT-compliant cloud accounting for Saudi SMEs. Start your free trial today.", '
    '"seo_og_title": "Cloud Accounting for Saudi SMEs", '
    '"seo_og_description": "Books, VAT invoicing, ZATCA compliance — built for Saudi SMEs.", '
    '"seo_keywords": "cloud accounting, saudi, zatca, vat", '
    '"explanation": "Derived from the page H1 and body.", "confidence": 0.9}'
)


@tagged('post_install', '-at_install')
class TestFillSeo(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Page = cls.env['website.page']
        cls.Log = cls.env['era.seo.ai.fix.log']
        cls.env['ir.config_parameter'].sudo().set_param('era_seo.ai_enabled', 'True')
        cls.env.user.groups_id = [(4, cls.env.ref(
            'era_seo_manager.group_era_seo_manager').id)]

    def _make_page(self, url='/fill-test', **vals):
        view = self.env['ir.ui.view'].create({
            'name': 'fill test view',
            'type': 'qweb',
            'arch': '<div><h1>Cloud Accounting for Saudi SMEs</h1>'
                    '<p>VAT invoicing, ZATCA compliance.</p></div>',
            'key': 'era_seo_ai.fill_view_' + url.replace('/', '_'),
        })
        vals['view_id'] = view.id
        vals['url'] = url
        return self.Page.create(vals)

    def test_fill_empty_fields(self):
        page = self._make_page(url='/fill-empty')
        with patch.object(AIClient, '_resolve_agent', return_value=_mock_agent(_FULL_REPLY)):
            page.action_ai_fill_seo()
        page.invalidate_recordset()
        self.assertEqual(page.seo_title, 'Cloud Accounting for Saudi SMEs | ERA')
        self.assertTrue(page.seo_description)
        self.assertTrue(page.seo_og_title)
        self.assertEqual(page.seo_keywords, 'cloud accounting, saudi, zatca, vat')

    def test_fill_does_not_overwrite_existing(self):
        page = self._make_page(url='/fill-keep')
        page.write({'seo_title': 'Hand-written Title'})
        with patch.object(AIClient, '_resolve_agent', return_value=_mock_agent(_FULL_REPLY)):
            page.action_ai_fill_seo()
        page.invalidate_recordset()
        # Title was already set -> preserved; description was empty -> filled.
        self.assertEqual(page.seo_title, 'Hand-written Title')
        self.assertTrue(page.seo_description)

    def test_rewrite_overwrites_existing(self):
        page = self._make_page(url='/fill-rewrite')
        page.write({'seo_title': 'Old Title'})
        with patch.object(AIClient, '_resolve_agent', return_value=_mock_agent(_FULL_REPLY)):
            page.action_ai_rewrite_seo()
        page.invalidate_recordset()
        self.assertEqual(page.seo_title, 'Cloud Accounting for Saudi SMEs | ERA')

    def test_fill_logs_with_kind_fill(self):
        page = self._make_page(url='/fill-log')
        before = self.Log.search_count([])
        with patch.object(AIClient, '_resolve_agent', return_value=_mock_agent(_FULL_REPLY)):
            page.action_ai_fill_seo()
        self.assertEqual(self.Log.search_count([]), before + 1)
        log = self.Log.search([], order='id desc', limit=1)
        self.assertEqual(log.kind, 'fill')
        self.assertEqual(log.target_model, 'website.page')
        self.assertTrue(log.applied)

    def test_fill_bad_json_logs_error_no_write(self):
        page = self._make_page(url='/fill-badjson')
        with patch.object(AIClient, '_resolve_agent', return_value=_mock_agent('not json')):
            page.action_ai_fill_seo()
        page.invalidate_recordset()
        self.assertFalse(page.seo_title)
        log = self.Log.search([('target_id', '=', page.id), ('kind', '=', 'fill')],
                              order='id desc', limit=1)
        self.assertTrue(log.error_message)

    def test_fill_batch_multiple_pages(self):
        p1 = self._make_page(url='/fill-batch-1')
        p2 = self._make_page(url='/fill-batch-2')
        pages = p1 | p2
        with patch.object(AIClient, '_resolve_agent', return_value=_mock_agent(_FULL_REPLY)):
            pages.action_ai_fill_seo()
        (p1 | p2).invalidate_recordset()
        self.assertTrue(p1.seo_title)
        self.assertTrue(p2.seo_title)

    def test_fill_requires_manager_group(self):
        page = self._make_page(url='/fill-acl')
        # Drop the manager group for this test.
        self.env.user.groups_id = [(3, self.env.ref(
            'era_seo_manager.group_era_seo_manager').id)]
        from odoo.exceptions import AccessError
        try:
            with patch.object(AIClient, '_resolve_agent', return_value=_mock_agent(_FULL_REPLY)):
                with self.assertRaises(AccessError):
                    page.action_ai_fill_seo()
        finally:
            self.env.user.groups_id = [(4, self.env.ref(
                'era_seo_manager.group_era_seo_manager').id)]

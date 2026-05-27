"""Tests for the AI auto-fix workflow.

The Odoo ``ai.agent`` is mocked by patching ``AIClient._resolve_agent`` so
tests don't need a configured LLM provider, API key, or network. The mock
agent exposes ``get_direct_response`` (returns a list of strings, matching
the real signature) plus ``name`` / ``llm_model`` attributes.
"""
from unittest.mock import MagicMock, patch

from odoo.tests import TransactionCase, tagged

from odoo.addons.era_seo_ai.models.ai_client import AIClient


def _mock_agent(reply_json):
    """Build a MagicMock that quacks like an ai.agent record."""
    agent = MagicMock()
    agent.name = 'Test SEO Agent'
    agent.llm_model = 'gpt-test'
    agent.get_direct_response.return_value = [reply_json]
    return agent


@tagged('post_install', '-at_install')
class TestAIWorkflow(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Finding = cls.env['era.seo.audit.finding']
        cls.Log = cls.env['era.seo.ai.fix.log']
        cls.Run = cls.env['era.seo.audit.run']
        cls.Page = cls.env['website.page']
        ICP = cls.env['ir.config_parameter'].sudo()
        ICP.set_param('era_seo.ai_enabled', 'True')
        cls.env.user.write({'groups_id': [(4, cls.env.ref(
            'era_seo_manager.group_era_seo_manager').id)]})

    def _make_page(self, url='/ai-test', name='AI Test', content=None):
        view = self.env['ir.ui.view'].create({
            'name': 'AI test view',
            'type': 'qweb',
            'arch': content or '<div><h1>Cloud Accounting for Saudi SMEs</h1>'
                               '<p>VAT-ready invoicing, ZATCA compliant.</p></div>',
            'key': 'era_seo_ai.test_view_' + url.replace('/', '_'),
        })
        return self.Page.create({
            'view_id': view.id,
            'url': url,
            'website_meta_title': name,
        })

    def _make_finding(self, page, check_code='missing_seo_title', severity='critical'):
        run = self.Run.create({})
        return self.Finding.create({
            'run_id': run.id,
            'severity': severity,
            'check_code': check_code,
            'check_name': check_code.replace('_', ' ').title(),
            'res_model': 'website.page',
            'res_id': page.id,
            'url': page.url,
        })

    # --- ai_supported compute -------------------------------------------------

    def test_ai_supported_true(self):
        f = self._make_finding(self._make_page('/sup-1'), 'missing_seo_title')
        self.assertTrue(f.ai_supported)

    def test_ai_supported_false(self):
        f = self._make_finding(self._make_page('/sup-2'), 'missing_h1')
        self.assertFalse(f.ai_supported)

    # --- Mechanical fix (no agent call) --------------------------------------

    def test_slug_uppercase_mechanical(self):
        page = self._make_page(url='/Has-Caps')
        f = self._make_finding(page, 'slug_contains_uppercase')
        # Patch _resolve_agent so is_available() passes; the mechanical path
        # should not actually call get_direct_response.
        agent = _mock_agent('{}')
        with patch.object(AIClient, '_resolve_agent', return_value=agent):
            f.action_ai_suggest()
        f.invalidate_recordset()
        self.assertEqual(f.ai_status, 'suggested')
        self.assertEqual(f.ai_proposed_value, '/has-caps')
        self.assertEqual(f.ai_model_used, 'mechanical')
        agent.get_direct_response.assert_not_called()

    # --- Full agent-mocked workflow ------------------------------------------

    def test_suggest_writes_proposal_and_log(self):
        page = self._make_page(url='/suggest-test')
        f = self._make_finding(page, 'missing_seo_title')
        agent = _mock_agent(
            '{"proposed_value": "New AI Title", '
            '"explanation": "Keyword first.", "confidence": 0.91}'
        )
        before = self.Log.search_count([])
        with patch.object(AIClient, '_resolve_agent', return_value=agent):
            f.action_ai_suggest()
        f.invalidate_recordset()
        self.assertEqual(f.ai_status, 'suggested')
        self.assertEqual(f.ai_proposed_value, 'New AI Title')
        self.assertEqual(f.ai_proposed_field, 'seo_title')
        self.assertAlmostEqual(f.ai_confidence, 0.91, places=2)
        self.assertEqual(self.Log.search_count([]), before + 1)
        # Called at least once (once per installed website language).
        agent.get_direct_response.assert_called()

    def test_suggest_tolerates_code_fences(self):
        page = self._make_page(url='/fence-test')
        f = self._make_finding(page, 'missing_seo_title')
        agent = _mock_agent(
            '```json\n{"proposed_value": "Fenced", "explanation": "x", "confidence": 0.8}\n```'
        )
        with patch.object(AIClient, '_resolve_agent', return_value=agent):
            f.action_ai_suggest()
        f.invalidate_recordset()
        self.assertEqual(f.ai_proposed_value, 'Fenced')

    def test_suggest_tolerates_prose_around_json(self):
        page = self._make_page(url='/prose-test')
        f = self._make_finding(page, 'missing_seo_title')
        agent = _mock_agent(
            'Sure! Here is the result: '
            '{"proposed_value": "Prose", "explanation": "x", "confidence": 0.7} '
            'Hope that helps.'
        )
        with patch.object(AIClient, '_resolve_agent', return_value=agent):
            f.action_ai_suggest()
        f.invalidate_recordset()
        self.assertEqual(f.ai_proposed_value, 'Prose')

    def test_apply_writes_value_and_resolves(self):
        page = self._make_page(url='/apply-test')
        f = self._make_finding(page, 'missing_seo_title')
        agent = _mock_agent(
            '{"proposed_value": "Applied Title", "explanation": "x", "confidence": 0.9}'
        )
        with patch.object(AIClient, '_resolve_agent', return_value=agent):
            f.action_ai_suggest()
        f.action_ai_apply()
        page.invalidate_recordset()
        f.invalidate_recordset()
        self.assertEqual(page.seo_title, 'Applied Title')
        self.assertEqual(f.ai_status, 'applied')
        self.assertTrue(f.is_resolved)

    def test_apply_writes_all_languages(self):
        """A missing-title fix applies to every installed website language."""
        fr = self.env.ref('base.lang_fr')
        fr.active = True
        website = self.env['website'].search([], limit=1)
        website.language_ids = [(4, self.env.ref('base.lang_en').id), (4, fr.id)]
        page = self._make_page(url='/apply-multilang')
        page.website_id = website.id
        f = self._make_finding(page, 'missing_seo_title')
        agent = _mock_agent(
            '{"proposed_value": "ML Title", "explanation": "x", "confidence": 0.9}')
        with patch.object(AIClient, '_resolve_agent', return_value=agent):
            f.action_ai_suggest()
        f.invalidate_recordset()
        self.assertTrue(f.ai_proposed_translations, 'Translations dict must be stored.')
        f.action_ai_apply()
        page.invalidate_recordset()
        self.assertTrue(page.with_context(lang='en_US').seo_title)
        self.assertTrue(page.with_context(lang='fr_FR').seo_title)

    def test_suggest_and_apply_confidence_threshold(self):
        page_hi = self._make_page(url='/hi-conf')
        page_lo = self._make_page(url='/lo-conf')
        f_hi = self._make_finding(page_hi, 'missing_seo_title')
        f_lo = self._make_finding(page_lo, 'missing_seo_title')

        with patch.object(AIClient, '_resolve_agent', return_value=_mock_agent(
            '{"proposed_value": "High", "explanation": "x", "confidence": 0.9}')):
            f_hi.action_ai_suggest_and_apply()
        f_hi.invalidate_recordset()
        page_hi.invalidate_recordset()
        self.assertEqual(f_hi.ai_status, 'applied')
        self.assertEqual(page_hi.seo_title, 'High')

        with patch.object(AIClient, '_resolve_agent', return_value=_mock_agent(
            '{"proposed_value": "Low", "explanation": "x", "confidence": 0.5}')):
            f_lo.action_ai_suggest_and_apply()
        f_lo.invalidate_recordset()
        page_lo.invalidate_recordset()
        self.assertEqual(f_lo.ai_status, 'suggested')
        self.assertFalse(page_lo.seo_title)

    def test_bad_json_records_failure(self):
        page = self._make_page(url='/badjson')
        f = self._make_finding(page, 'missing_seo_title')
        agent = _mock_agent('this is not json at all')
        with patch.object(AIClient, '_resolve_agent', return_value=agent):
            f.action_ai_suggest()
        f.invalidate_recordset()
        self.assertEqual(f.ai_status, 'failed')
        log = self.Log.search([('finding_id', '=', f.id)], limit=1)
        self.assertTrue(log.error_message)

    def test_disabled_blocks_calls(self):
        self.env['ir.config_parameter'].sudo().set_param('era_seo.ai_enabled', 'False')
        page = self._make_page(url='/disabled')
        f = self._make_finding(page, 'missing_seo_title')
        from odoo.exceptions import UserError
        with self.assertRaises(UserError):
            f.action_ai_suggest()
        # Re-enable for any later tests in the class.
        self.env['ir.config_parameter'].sudo().set_param('era_seo.ai_enabled', 'True')

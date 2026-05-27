"""Tests for the AI auto-fix workflow.

The Anthropic SDK is mocked so tests don't need an API key or network.
Patches target the module-level ``anthropic`` import inside ai_client so
the actual SDK never gets touched.
"""
import json
from unittest.mock import MagicMock, patch

from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestAIWorkflow(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Finding = cls.env['era.seo.audit.finding']
        cls.Log = cls.env['era.seo.ai.fix.log']
        cls.Run = cls.env['era.seo.audit.run']
        cls.Page = cls.env['website.page']
        # Enable AI globally for tests
        ICP = cls.env['ir.config_parameter'].sudo()
        ICP.set_param('era_seo.ai_enabled', 'True')
        ICP.set_param('era_seo.ai_api_key', 'sk-test-12345')
        ICP.set_param('era_seo.ai_model', 'claude-haiku-4-5')
        # Make sure the running user is a SEO manager so the action passes
        # the access check inside _check_manager.
        cls.env.user.groups_id = [(4, cls.env.ref(
            'era_seo_manager.group_era_seo_manager').id)]

    def _make_page(self, url='/ai-test', name='AI Test', content=None):
        view = self.env['ir.ui.view'].create({
            'name': 'AI test view',
            'type': 'qweb',
            'arch': content or '<div><h1>Cloud Accounting for Saudi SMEs</h1>'
                               '<p>Manage your books, VAT-ready invoicing.</p></div>',
            'key': 'era_seo_ai.test_view_' + url.replace('/', '_'),
        })
        return self.Page.create({
            'view_id': view.id,
            'url': url,
            'website_meta_title': name,
        })

    def _make_finding(self, page, check_code='missing_seo_title',
                     severity='critical'):
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

    def test_ai_supported_true_for_fixable_codes(self):
        page = self._make_page(url='/sup-1')
        f = self._make_finding(page, 'missing_seo_title')
        self.assertTrue(f.ai_supported)

    def test_ai_supported_false_for_unsupported_codes(self):
        page = self._make_page(url='/sup-2')
        f = self._make_finding(page, 'missing_h1')
        self.assertFalse(f.ai_supported)

    # --- Mechanical fix (no API call) ----------------------------------------

    def test_slug_uppercase_mechanical_fix(self):
        page = self._make_page(url='/Has-Caps-Slug')
        f = self._make_finding(page, 'slug_contains_uppercase')
        # No anthropic mock needed — this code path takes a mechanical fix.
        f.action_ai_suggest()
        f.invalidate_recordset()
        self.assertEqual(f.ai_status, 'suggested')
        self.assertEqual(f.ai_proposed_value, '/has-caps-slug')
        self.assertEqual(f.ai_proposed_field, 'url')
        self.assertEqual(f.ai_model_used, 'mechanical')

    # --- Full Claude-mocked workflow -----------------------------------------

    def _build_mock_anthropic_response(
        self,
        proposed_value='Cloud Accounting for Saudi SMEs | ERA',
        explanation='Lead with the primary keyword.',
        confidence=0.92,
        cache_read=0,
        cache_creation=0,
    ):
        """Construct a MagicMock that quacks like Anthropic's SDK response."""
        text_block = MagicMock()
        text_block.type = 'text'
        text_block.text = json.dumps({
            'proposed_value': proposed_value,
            'explanation': explanation,
            'confidence': confidence,
        })
        response = MagicMock()
        response.content = [text_block]
        response.usage.input_tokens = 5000
        response.usage.output_tokens = 50
        response.usage.cache_read_input_tokens = cache_read
        response.usage.cache_creation_input_tokens = cache_creation
        return response

    def _patch_anthropic(self, response):
        """Patch the anthropic SDK at the import site inside ai_client."""
        fake_sdk = MagicMock()
        fake_sdk.Anthropic.return_value.messages.create.return_value = response
        return patch.dict('sys.modules', {'anthropic': fake_sdk})

    def test_suggest_writes_proposal_and_log(self):
        page = self._make_page(url='/suggest-test', name='OldTitle')
        f = self._make_finding(page, 'missing_seo_title')
        response = self._build_mock_anthropic_response(
            proposed_value='New AI Title', confidence=0.91, cache_creation=4096,
        )
        before_logs = self.Log.search_count([])
        with self._patch_anthropic(response):
            f.action_ai_suggest()
        f.invalidate_recordset()
        self.assertEqual(f.ai_status, 'suggested')
        self.assertEqual(f.ai_proposed_value, 'New AI Title')
        self.assertEqual(f.ai_proposed_field, 'seo_title')
        self.assertAlmostEqual(f.ai_confidence, 0.91, places=2)
        self.assertEqual(self.Log.search_count([]), before_logs + 1)
        last_log = self.Log.search([], order='id desc', limit=1)
        self.assertEqual(last_log.proposed_value, 'New AI Title')
        self.assertEqual(last_log.input_tokens, 5000)
        self.assertFalse(last_log.cache_hit)  # first call -> no cache read

    def test_suggest_records_cache_hit(self):
        page = self._make_page(url='/cache-hit-test')
        f = self._make_finding(page, 'missing_seo_title')
        response = self._build_mock_anthropic_response(cache_read=4800)
        with self._patch_anthropic(response):
            f.action_ai_suggest()
        log = self.Log.search([], order='id desc', limit=1)
        self.assertTrue(log.cache_hit)
        self.assertEqual(log.cache_read_input_tokens, 4800)

    def test_apply_writes_value_and_resolves(self):
        page = self._make_page(url='/apply-test')
        f = self._make_finding(page, 'missing_seo_title')
        response = self._build_mock_anthropic_response(
            proposed_value='Applied Title')
        with self._patch_anthropic(response):
            f.action_ai_suggest()
        f.action_ai_apply()
        page.invalidate_recordset()
        f.invalidate_recordset()
        self.assertEqual(page.seo_title, 'Applied Title')
        self.assertEqual(f.ai_status, 'applied')
        self.assertTrue(f.is_resolved)

    def test_suggest_and_apply_respects_confidence_threshold(self):
        page_high = self._make_page(url='/high-conf')
        page_low = self._make_page(url='/low-conf')
        f_high = self._make_finding(page_high, 'missing_seo_title')
        f_low = self._make_finding(page_low, 'missing_seo_title')

        # First call high confidence
        with self._patch_anthropic(self._build_mock_anthropic_response(
            proposed_value='High', confidence=0.9)):
            f_high.action_ai_suggest_and_apply()
        f_high.invalidate_recordset()
        page_high.invalidate_recordset()
        self.assertEqual(f_high.ai_status, 'applied')
        self.assertEqual(page_high.seo_title, 'High')

        # Then low confidence — suggest but don't auto-apply
        with self._patch_anthropic(self._build_mock_anthropic_response(
            proposed_value='Low', confidence=0.5)):
            f_low.action_ai_suggest_and_apply()
        f_low.invalidate_recordset()
        page_low.invalidate_recordset()
        self.assertEqual(f_low.ai_status, 'suggested')
        self.assertFalse(page_low.seo_title)

    def test_failed_suggestion_records_log(self):
        page = self._make_page(url='/fail-test')
        f = self._make_finding(page, 'missing_seo_title')

        fake_sdk = MagicMock()
        fake_sdk.Anthropic.return_value.messages.create.side_effect = \
            RuntimeError('API outage')

        with patch.dict('sys.modules', {'anthropic': fake_sdk}):
            f.action_ai_suggest()

        f.invalidate_recordset()
        self.assertEqual(f.ai_status, 'failed')
        log = self.Log.search([('finding_id', '=', f.id)], limit=1)
        self.assertIn('API outage', log.error_message or '')

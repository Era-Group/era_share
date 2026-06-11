"""Tests for the era_geo_ai bridge — AI fix for geo_no_answer_summary."""
from unittest.mock import MagicMock, patch

from odoo.tests import TransactionCase, tagged

from odoo.addons.era_seo_suite.models.ai_client import AIClient


def _mock_agent(reply_json):
    agent = MagicMock()
    agent.name = 'Test SEO Agent'
    agent.llm_model = 'gpt-test'
    agent.get_direct_response.return_value = [reply_json]
    return agent


# geo_answer_summary is translate=True, so the suggest flow uses the
# consolidated multi-language contract (one AI call for all languages):
#   {"by_lang": {code: value, ...}, "explanation": ..., "confidence": ...}
_REPLY = (
    '{"by_lang": {"en_US": '
    '"ERA delivers bilingual SEO and GEO for Saudi SMEs."}, '
    '"explanation": "Quotable one-liner.", "confidence": 0.92}'
)


@tagged('post_install', '-at_install')
class TestGeoAiFix(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Finding = cls.env['era.seo.audit.finding']
        cls.Run = cls.env['era.seo.audit.run']
        cls.Page = cls.env['website.page']
        cls.env['ir.config_parameter'].sudo().set_param(
            'era_seo.ai_enabled', 'True')
        cls.env.user.write({'group_ids': [(4, cls.env.ref(
            'era_seo_suite.group_era_seo_manager').id)]})

    def _make_page(self, url='/geo-ai-test'):
        view = self.env['ir.ui.view'].create({
            'name': 'geo ai test view',
            'type': 'qweb',
            'arch': '<div><h1>Cloud Accounting</h1>'
                    '<p>VAT-ready, ZATCA compliant.</p></div>',
            'key': 'era_seo_suite.test_view_' + url.replace('/', '_'),
        })
        return self.Page.create({'view_id': view.id, 'url': url})

    def _make_finding(self, page):
        run = self.Run.create({})
        return self.Finding.create({
            'run_id': run.id,
            'severity': 'info',
            'check_code': 'geo_no_answer_summary',
            'check_name': 'GEO: Missing answer summary',
            'res_model': 'website.page',
            'res_id': page.id,
            'url': page.url,
        })

    def test_geo_finding_is_ai_supported(self):
        f = self._make_finding(self._make_page('/geo-sup'))
        self.assertTrue(
            f.ai_supported,
            'geo_no_answer_summary must be AI-fixable once era_geo_ai is loaded')

    def test_suggest_writes_proposal_for_geo_summary(self):
        page = self._make_page('/geo-suggest')
        f = self._make_finding(page)
        with patch.object(AIClient, '_resolve_agent',
                          return_value=_mock_agent(_REPLY)):
            f.action_ai_suggest()
        f.invalidate_recordset()
        self.assertEqual(f.ai_status, 'suggested')
        self.assertEqual(f.ai_proposed_field, 'geo_answer_summary')
        self.assertIn('ERA delivers', f.ai_proposed_value)

    def test_apply_writes_geo_answer_summary(self):
        page = self._make_page('/geo-apply')
        f = self._make_finding(page)
        with patch.object(AIClient, '_resolve_agent',
                          return_value=_mock_agent(_REPLY)):
            f.action_ai_suggest()
        f.action_ai_apply()
        page.invalidate_recordset()
        f.invalidate_recordset()
        self.assertTrue(page.geo_answer_summary,
                        'geo_answer_summary must be set after Apply')
        self.assertIn('ERA delivers', page.geo_answer_summary)
        self.assertEqual(f.ai_status, 'applied')
        self.assertTrue(f.is_resolved)

"""Tests for the richer AI fixes (19.0.7.0.0):

  - missing_og_image   -> mechanical: set the OG image to the company logo
  - missing_schema     -> AI picks a JSON-LD template, attaches an instance
  - image_missing_alt  -> AI writes alt text, injected into the content imgs
  - thin_content       -> AI proposes an HTML block, appended on apply

The ``ai.agent`` is mocked via ``AIClient._resolve_agent`` so no LLM
provider / network is required.
"""
from unittest.mock import MagicMock, patch

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

from odoo.addons.era_seo_ai.models.ai_client import AIClient

# 1x1 transparent PNG, base64 — a valid value for a Binary image field.
_PNG_1PX = (
    b'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR42mNk'
    b'+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=='
)


def _mock_agent(reply_json):
    agent = MagicMock()
    agent.name = 'Test SEO Agent'
    agent.llm_model = 'gpt-test'
    agent.get_direct_response.return_value = [reply_json]
    return agent


@tagged('post_install', '-at_install')
class TestRichFixes(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Finding = cls.env['era.seo.audit.finding']
        cls.Run = cls.env['era.seo.audit.run']
        cls.Page = cls.env['website.page']
        cls.Instance = cls.env['era.seo.schema.instance']
        cls.Template = cls.env['era.seo.schema.template']
        ICP = cls.env['ir.config_parameter'].sudo()
        ICP.set_param('era_seo.ai_enabled', 'True')
        cls.env.user.groups_id = [(4, cls.env.ref(
            'era_seo_manager.group_era_seo_manager').id)]
        cls.env.company.logo = _PNG_1PX

    def _make_page(self, url, arch=None):
        arch = arch or (
            '<div><h1>Cloud Accounting for Saudi SMEs</h1>'
            '<p>VAT-ready invoicing, ZATCA compliant.</p></div>'
        )
        view = self.env['ir.ui.view'].create({
            'name': 'rich fix view',
            'type': 'qweb',
            'arch': arch,
            'key': 'era_seo_ai.rich_view_' + url.replace('/', '_'),
        })
        return self.Page.create({
            'view_id': view.id,
            'url': url,
            'website_meta_title': 'Rich Fix',
        })

    def _make_finding(self, page, check_code, severity='warning'):
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

    # --- ai_supported now covers the four new codes --------------------------

    def test_new_codes_are_ai_supported(self):
        for code in ('missing_og_image', 'missing_schema',
                     'image_missing_alt', 'thin_content'):
            f = self._make_finding(self._make_page('/sup-' + code), code)
            self.assertTrue(f.ai_supported, '%s should be AI-fixable' % code)

    # --- OG image (mechanical) -----------------------------------------------

    def test_og_image_mechanical_no_agent_call(self):
        page = self._make_page('/og-img')
        f = self._make_finding(page, 'missing_og_image')
        agent = _mock_agent('{}')
        with patch.object(AIClient, '_resolve_agent', return_value=agent):
            f.action_ai_suggest()
        f.invalidate_recordset()
        self.assertEqual(f.ai_status, 'suggested')
        self.assertEqual(f.ai_fix_type, 'og_image')
        self.assertEqual(f.ai_model_used, 'mechanical')
        self.assertAlmostEqual(f.ai_confidence, 1.0, places=2)
        agent.get_direct_response.assert_not_called()

    def test_og_image_apply_sets_company_logo(self):
        page = self._make_page('/og-img-apply')
        f = self._make_finding(page, 'missing_og_image')
        with patch.object(AIClient, '_resolve_agent', return_value=_mock_agent('{}')):
            f.action_ai_suggest()
        f.action_ai_apply()
        page.invalidate_recordset()
        f.invalidate_recordset()
        self.assertTrue(page.seo_og_image, 'OG image must be set after apply.')
        self.assertEqual(page.seo_og_image, self.env.company.logo)
        self.assertEqual(f.ai_status, 'applied')
        self.assertTrue(f.is_resolved)

    # --- Schema attach -------------------------------------------------------

    def test_schema_suggest_and_apply_creates_instance(self):
        tpl = self.Template.create({
            'name': 'Test Article',
            'code': 'test_article_fix',
            'schema_type': 'Article',
            'body': '{"@context": "https://schema.org", "@type": "Article", '
                    '"headline": "{{ record.seo_title }}"}',
        })
        page = self._make_page('/schema-page')
        f = self._make_finding(page, 'missing_schema')
        agent = _mock_agent(
            '{"template_code": "test_article_fix", '
            '"explanation": "It is an article.", "confidence": 0.9}')
        with patch.object(AIClient, '_resolve_agent', return_value=agent):
            f.action_ai_suggest()
        f.invalidate_recordset()
        self.assertEqual(f.ai_fix_type, 'schema')
        self.assertIn('test_article_fix', f.ai_fix_payload)
        f.action_ai_apply()
        f.invalidate_recordset()
        self.assertEqual(f.ai_status, 'applied')
        inst = self.Instance.search([
            ('res_model', '=', 'website.page'),
            ('res_id', '=', page.id),
            ('template_id', '=', tpl.id),
        ])
        self.assertEqual(len(inst), 1, 'Exactly one schema instance attached.')

    def test_schema_rejects_invented_code(self):
        self.Template.create({
            'name': 'Org', 'code': 'real_code_only', 'schema_type': 'Organization',
            'body': '{"@context": "https://schema.org", "@type": "Organization"}',
        })
        page = self._make_page('/schema-bad')
        f = self._make_finding(page, 'missing_schema')
        agent = _mock_agent(
            '{"template_code": "made_up_code", "confidence": 0.9}')
        with patch.object(AIClient, '_resolve_agent', return_value=agent):
            f.action_ai_suggest()
        f.invalidate_recordset()
        # Invalid choice -> the suggest call records a failure, not a proposal.
        self.assertEqual(f.ai_status, 'failed')

    # --- Image alt text ------------------------------------------------------

    def test_image_alt_inject_plain_arch(self):
        page = self._make_page(
            '/alt-html',
            arch='<div><h1>Our Team</h1>'
                 '<img src="/web/image/1/team.png"/>'
                 '<p>Meet the people behind ERA.</p></div>',
        )
        f = self._make_finding(page, 'image_missing_alt')
        agent = _mock_agent(
            '{"alts": ["The ERA consulting team in the Riyadh office"], '
            '"explanation": "Describes the photo.", "confidence": 0.88}')
        with patch.object(AIClient, '_resolve_agent', return_value=agent):
            f.action_ai_suggest()
        f.invalidate_recordset()
        self.assertEqual(f.ai_fix_type, 'image_alt')
        f.action_ai_apply()
        page.invalidate_recordset()
        f.invalidate_recordset()
        self.assertEqual(f.ai_status, 'applied')
        self.assertIn('Riyadh office', page.arch)
        self.assertIn('alt=', page.arch)

    def test_image_alt_inject_qweb_arch_path(self):
        page = self._make_page(
            '/alt-qweb',
            arch='<t t-name="era_seo_ai.alt_qweb">'
                 '<div><h1>Gallery</h1><img src="/p.png"/></div></t>',
        )
        f = self._make_finding(page, 'image_missing_alt')
        agent = _mock_agent(
            '{"alts": ["Project gallery thumbnail"], "confidence": 0.8}')
        with patch.object(AIClient, '_resolve_agent', return_value=agent):
            f.action_ai_suggest()
        f.action_ai_apply()
        page.invalidate_recordset()
        f.invalidate_recordset()
        self.assertEqual(f.ai_status, 'applied')
        self.assertIn('Project gallery thumbnail', page.arch)

    def test_image_alt_no_missing_images_fails_gracefully(self):
        page = self._make_page(
            '/alt-none',
            arch='<div><h1>No imgs</h1><p>text only</p></div>')
        f = self._make_finding(page, 'image_missing_alt')
        agent = _mock_agent('{"alts": ["x"], "confidence": 0.9}')
        with patch.object(AIClient, '_resolve_agent', return_value=agent):
            f.action_ai_suggest()
        f.invalidate_recordset()
        # No images to caption -> recorded as a failure, nothing applied.
        self.assertEqual(f.ai_status, 'failed')

    # --- Thin content --------------------------------------------------------

    def test_thin_content_confidence_capped(self):
        page = self._make_page('/thin-cap')
        f = self._make_finding(page, 'thin_content')
        agent = _mock_agent(
            '{"html": "<h2>How it works</h2><p>Step by step.</p>", '
            '"explanation": "Adds depth.", "confidence": 0.95}')
        with patch.object(AIClient, '_resolve_agent', return_value=agent):
            f.action_ai_suggest()
        f.invalidate_recordset()
        self.assertEqual(f.ai_fix_type, 'thin_content')
        self.assertLessEqual(f.ai_confidence, 0.6,
                             'Thin-content confidence must be capped for review.')

    def test_thin_content_not_auto_applied(self):
        """Suggest+Auto-Apply must NOT silently inject body copy."""
        page = self._make_page('/thin-auto')
        f = self._make_finding(page, 'thin_content')
        agent = _mock_agent(
            '{"html": "<h2>More</h2><p>Body.</p>", "confidence": 0.95}')
        with patch.object(AIClient, '_resolve_agent', return_value=agent):
            f.action_ai_suggest_and_apply()
        page.invalidate_recordset()
        f.invalidate_recordset()
        self.assertEqual(f.ai_status, 'suggested', 'Should stay un-applied.')
        self.assertNotIn('Body.', page.arch)

    def test_thin_content_manual_apply_appends_html(self):
        page = self._make_page(
            '/thin-apply',
            arch='<t t-name="era_seo_ai.thin"><div><h1>Topic</h1>'
                 '<p>Short.</p></div></t>')
        f = self._make_finding(page, 'thin_content')
        agent = _mock_agent(
            '{"html": "<h2>Benefits</h2><p>Saves time.</p>", "confidence": 0.9}')
        with patch.object(AIClient, '_resolve_agent', return_value=agent):
            f.action_ai_suggest()
        f.action_ai_apply()
        page.invalidate_recordset()
        f.invalidate_recordset()
        self.assertEqual(f.ai_status, 'applied')
        self.assertIn('Benefits', page.arch)
        self.assertIn('Saves time.', page.arch)
        # Original content preserved.
        self.assertIn('Short.', page.arch)

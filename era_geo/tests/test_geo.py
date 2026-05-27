"""Tests for era_geo — AI crawler model, robots.txt, and /llms.txt."""
from odoo.tests import HttpCase, TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestGeoCrawlerModel(TransactionCase):

    def setUp(self):
        super().setUp()
        self.Crawler = self.env['era.geo.ai.crawler']

    def test_robots_block_allow_and_block(self):
        # Use the seeded crawlers; flip one to blocked.
        gptbot = self.env.ref('era_geo.crawler_gptbot')
        gptbot.allowed = False
        perplexity = self.env.ref('era_geo.crawler_perplexitybot')
        perplexity.allowed = True

        block = self.Crawler._robots_block()
        self.assertIn('User-agent: GPTBot', block)
        self.assertIn('User-agent: PerplexityBot', block)
        # The blocked one must emit Disallow, the allowed one Allow.
        gpt_idx = block.index('User-agent: GPTBot')
        self.assertIn('Disallow: /', block[gpt_idx:gpt_idx + 40])
        ppx_idx = block.index('User-agent: PerplexityBot')
        self.assertIn('Allow: /', block[ppx_idx:ppx_idx + 40])

    def test_user_agent_unique(self):
        self.Crawler.create({'name': 'X', 'user_agent': 'UniqueUA1'})
        with self.assertRaises(Exception):
            with self.env.cr.savepoint():
                self.Crawler.create({'name': 'Y', 'user_agent': 'UniqueUA1'})

    def test_empty_when_no_crawlers(self):
        self.Crawler.search([]).unlink()
        self.assertEqual(self.Crawler._robots_block(), '')


@tagged('post_install', '-at_install')
class TestGeoEndpoints(HttpCase):

    def _icp(self, key, value):
        self.env['ir.config_parameter'].sudo().set_param(key, value)

    def test_robots_includes_ai_crawlers(self):
        res = self.url_open('/robots.txt')
        self.assertEqual(res.status_code, 200)
        self.assertIn('GPTBot', res.text)
        self.assertIn('ERA GEO', res.text)

    def test_llms_txt_served(self):
        self._icp('era_geo.llms_enabled', 'True')
        res = self.url_open('/llms.txt')
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.headers.get('Content-Type', '').startswith('text/plain'))
        # Markdown heading present.
        self.assertTrue(res.text.lstrip().startswith('# '))

    def test_llms_txt_disabled_returns_404(self):
        self._icp('era_geo.llms_enabled', 'False')
        res = self.url_open('/llms.txt')
        self.assertEqual(res.status_code, 404)
        self._icp('era_geo.llms_enabled', 'True')

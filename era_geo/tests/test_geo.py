"""Tests for era_geo — AI crawler model, robots.txt, /llms.txt, GEO audit."""
from odoo.tests import HttpCase, TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestGeoAudit(TransactionCase):
    """GEO citability checks bolted onto the SEO audit run."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Page = cls.env['website.page']
        cls.Run = cls.env['era.seo.audit.run']
        cls.Finding = cls.env['era.seo.audit.finding']
        cls.ICP = cls.env['ir.config_parameter'].sudo()

    def _make_page(self, url, arch):
        view = self.env['ir.ui.view'].create({
            'name': 'geo audit view',
            'type': 'qweb',
            'arch': arch,
            'key': 'era_geo.audit_view_' + url.replace('/', '_'),
        })
        return self.Page.create({'view_id': view.id, 'url': url})

    def _findings(self, run, code):
        return self.Finding.search([('run_id', '=', run.id),
                                    ('check_code', '=', code)])

    def test_llms_disabled_flags_finding(self):
        self._make_page('/', '<div><h1>Home</h1><h2>About</h2><p>x</p></div>')
        self.ICP.set_param('era_geo.llms_enabled', 'False')
        run = self.Run.create({})
        run._run_audit()
        self.assertTrue(self._findings(run, 'geo_llms_txt_disabled'))
        self.ICP.set_param('era_geo.llms_enabled', 'True')

    def test_blocked_answer_bot_flags_finding(self):
        self._make_page('/', '<div><h1>Home</h1><h2>About</h2><p>x</p></div>')
        bot = self.env.ref('era_geo.crawler_perplexitybot')
        bot.allowed = False
        run = self.Run.create({})
        run._run_audit()
        self.assertTrue(self._findings(run, 'geo_answer_bots_blocked'))
        bot.allowed = True

    def test_no_heading_structure_flags_finding(self):
        words = ' '.join(['word'] * 200)
        page = self._make_page('/geo-flat', '<div><p>%s</p></div>' % words)
        run = self.Run.create({})
        run._run_audit()
        f = self.Finding.search([
            ('run_id', '=', run.id),
            ('res_id', '=', page.id),
            ('check_code', '=', 'geo_no_heading_structure'),
        ])
        self.assertTrue(f)

    def test_headings_present_no_finding(self):
        words = ' '.join(['word'] * 200)
        page = self._make_page(
            '/geo-structured', '<div><h2>Intro</h2><p>%s</p></div>' % words)
        run = self.Run.create({})
        run._run_audit()
        f = self.Finding.search([
            ('run_id', '=', run.id),
            ('res_id', '=', page.id),
            ('check_code', '=', 'geo_no_heading_structure'),
        ])
        self.assertFalse(f)

    def test_long_page_without_answer_summary_flags(self):
        words = ' '.join(['word'] * 400)
        page = self._make_page(
            '/geo-long', '<div><h2>X</h2><p>%s</p></div>' % words)
        run = self.Run.create({})
        run._run_audit()
        f = self.Finding.search([
            ('run_id', '=', run.id),
            ('res_id', '=', page.id),
            ('check_code', '=', 'geo_no_answer_summary'),
        ])
        self.assertTrue(f)

    def test_short_page_without_summary_no_finding(self):
        words = ' '.join(['word'] * 50)  # < 300-word threshold
        page = self._make_page(
            '/geo-short', '<div><h2>X</h2><p>%s</p></div>' % words)
        run = self.Run.create({})
        run._run_audit()
        f = self.Finding.search([
            ('run_id', '=', run.id),
            ('res_id', '=', page.id),
            ('check_code', '=', 'geo_no_answer_summary'),
        ])
        self.assertFalse(f)

    def test_answer_summary_present_no_finding(self):
        words = ' '.join(['word'] * 400)
        page = self._make_page(
            '/geo-summarised', '<div><h2>X</h2><p>%s</p></div>' % words)
        page.geo_answer_summary = (
            'ERA provides bilingual SEO and GEO services for the Saudi market.')
        run = self.Run.create({})
        run._run_audit()
        f = self.Finding.search([
            ('run_id', '=', run.id),
            ('res_id', '=', page.id),
            ('check_code', '=', 'geo_no_answer_summary'),
        ])
        self.assertFalse(f)

    def test_geo_fields_in_ai_fill_specs(self):
        """The defensive _ai_fill_fields override adds GEO fields to the spec
        set when era_seo_ai contributes the base method."""
        block = self.env['era.content.block'].create({
            'name': 'GEO test block',
            'code': 'geo_test_block',
        })
        specs = block._ai_fill_fields()
        names = [s['name'] for s in specs]
        self.assertIn('geo_answer_summary', names)
        self.assertIn('geo_key_takeaways', names)


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

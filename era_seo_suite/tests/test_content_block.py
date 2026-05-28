"""Tests for Phase 8 — era.content.block model + snippet templates render."""
import datetime

from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged

_SNIPPETS = [
    'era_seo_suite.s_era_faq',
    'era_seo_suite.s_era_cta',
    'era_seo_suite.s_era_author_box',
    'era_seo_suite.s_era_related_posts',
    'era_seo_suite.s_era_breadcrumbs',
    'era_seo_suite.s_era_feature_grid',
    'era_seo_suite.s_era_pricing_table',
]


@tagged('post_install', '-at_install')
class TestContentBlock(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Block = cls.env['era.content.block']

    # --- Model ---------------------------------------------------------------

    def test_create_and_seo_mixin(self):
        block = self.Block.create({
            'name': 'Pricing FAQ',
            'code': 'pricing_faq',
            'block_type': 'faq',
            'seo_title': 'Pricing FAQ | ERA',
        })
        # Inherits era.seo.mixin -> seo fields work.
        self.assertEqual(block.seo_title, 'Pricing FAQ | ERA')
        self.assertEqual(block._get_seo_path(), '/content-block/pricing_faq')

    def test_code_unique(self):
        self.Block.create({'name': 'A', 'code': 'dup_code'})
        with self.assertRaises(Exception):
            with self.env.cr.savepoint():
                self.Block.create({'name': 'B', 'code': 'dup_code'})

    def test_code_charset_validated(self):
        with self.assertRaises(ValidationError):
            self.Block.create({'name': 'Bad', 'code': 'has spaces!'})

    def test_code_allows_hyphen_underscore(self):
        block = self.Block.create({'name': 'OK', 'code': 'my-block_01'})
        self.assertTrue(block.id)

    # --- Snippet templates render --------------------------------------------

    def test_all_snippets_render(self):
        """Every registered snippet template renders to non-empty HTML."""
        for xmlid in _SNIPPETS:
            html = self.env['ir.qweb']._render(xmlid, {'datetime': datetime})
            html = str(html)
            self.assertTrue(html.strip(), '%s rendered empty' % xmlid)
            # The root class encodes the snippet name; confirm it's present.
            cls = xmlid.split('.s_')[-1]
            self.assertIn('s_' + cls, html, '%s missing its root class' % xmlid)

    def test_faq_snippet_has_qa_hooks(self):
        """FAQ markup carries the hooks the JS injector reads."""
        html = str(self.env['ir.qweb']._render(
            'era_seo_suite.s_era_faq', {'datetime': datetime}))
        self.assertIn('s_era_faq_item', html)
        self.assertIn('s_era_faq_q', html)
        self.assertIn('s_era_faq_a', html)

    def test_breadcrumbs_snippet_has_list_hook(self):
        html = str(self.env['ir.qweb']._render('era_seo_suite.s_era_breadcrumbs'))
        self.assertIn('s_era_breadcrumbs_list', html)

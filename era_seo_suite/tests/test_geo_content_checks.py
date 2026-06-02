"""Tests for the GEO content-quality audit checks.

Covers the two checks added for AI citability: geo_no_faq (substantial page
with no FAQPage schema) and geo_low_factual_density (long page with almost no
concrete figures). Run post_install so era.seo.schema.instance and the FAQ
auto-attach hooks are fully live.
"""
from odoo.tests import tagged

from .common import EraSeoTestCase

# ~320 Arabic words, zero digits, no FAQ accordion → vague + FAQ-less.
_VAGUE_ARCH = '<div><p>' + ('خبرة ' * 320) + '</p></div>'

# Long page WITH figures and an FAQ accordion → should pass both checks.
_RICH_ARCH = (
    '<div><p>' + ('خبرة ' * 300) + ' '
    + 'تأسست 2000 وأنجزنا 250 مشروعاً بنسبة 99.9% خلال 10 أيام بسعر 680 ريال.'
    + '</p>'
    '<section class="s_faq_collapse"><div class="accordion">'
    '<div class="card"><a class="card-header">سؤال؟</a>'
    '<div class="card-body"><p>جواب مفصّل وواضح.</p></div></div>'
    '</div></section></div>'
)


@tagged('post_install', '-at_install')
class TestGeoContentChecks(EraSeoTestCase):

    def _make_page(self, arch, slug):
        view = self.env['ir.ui.view'].create({
            'name': 'GEO test view %s' % slug,
            'type': 'qweb',
            'arch': arch,
            'key': 'era_seo_suite.geo_test_%s' % slug,
        })
        return self.env['website.page'].create({
            'view_id': view.id, 'url': '/%s' % slug,
        })

    def _codes(self, run):
        return self.env['era.seo.audit.finding'].sudo().search(
            [('run_id', '=', run.id)]).mapped('check_code')

    def test_vague_page_flagged(self):
        page = self._make_page(_VAGUE_ARCH, 'geo_vague')
        run = self.env['era.seo.audit.run'].create({})
        run._check_geo_factual_density(page)
        run._check_geo_faq_schema(page)
        codes = self._codes(run)
        self.assertIn('geo_low_factual_density', codes)
        self.assertIn('geo_no_faq', codes)

    def test_rich_page_not_flagged(self):
        page = self._make_page(_RICH_ARCH, 'geo_rich')
        run = self.env['era.seo.audit.run'].create({})
        run._check_geo_factual_density(page)
        run._check_geo_faq_schema(page)
        codes = self._codes(run)
        self.assertNotIn('geo_low_factual_density', codes)
        # The FAQ accordion auto-attached a FAQPage instance on page create,
        # so the FAQ citability check must be satisfied.
        self.assertNotIn('geo_no_faq', codes)

    def test_checks_are_registered(self):
        run = self.env['era.seo.audit.run'].create({})
        names = [m.__name__ for m in run._get_check_methods()]
        self.assertIn('_check_geo_faq_schema', names)
        self.assertIn('_check_geo_factual_density', names)

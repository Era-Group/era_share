"""Tests for the AI GEO content review finding lifecycle.

Exercises _ai_geo_apply_issues directly (no LLM call): upsert geo_ai_* findings
from an issue list, and auto-resolve dimensions no longer flagged on re-run.
"""
from odoo.tests import tagged

from .common import EraSeoTestCase


@tagged('post_install', '-at_install')
class TestGeoAiReview(EraSeoTestCase):

    def _make_page(self, slug):
        view = self.env['ir.ui.view'].create({
            'name': 'geoai test %s' % slug, 'type': 'qweb',
            'arch': '<div>x</div>', 'key': 'era_seo_suite.geoai_%s' % slug,
        })
        return self.env['website.page'].create({
            'view_id': view.id, 'url': '/%s' % slug})

    def _open(self, page):
        return self.env['era.seo.audit.finding'].sudo().search([
            ('res_model', '=', 'website.page'), ('res_id', '=', page.id),
            ('check_code', '=like', 'geo_ai_%'), ('is_resolved', '=', False)])

    def test_apply_then_clear_on_rerun(self):
        page = self._make_page('geoai1')
        issues = [
            {'code': 'geo_ai_specificity', 'severity': 'info',
             'title': 'Add concrete figures', 'detail': 'vague',
             'recommendation': 'State years, clients, % and SLAs.'},
            {'code': 'geo_ai_proof', 'severity': 'warning',
             'title': 'Add text case studies', 'detail': 'logos only',
             'recommendation': 'Add 2 case studies with measurable results.'},
        ]
        n = page._ai_geo_apply_issues(issues)
        self.assertEqual(n, 2)
        found = self._open(page)
        self.assertEqual(set(found.mapped('check_code')),
                         {'geo_ai_specificity', 'geo_ai_proof'})
        # the warning carried its severity + suggested fix
        proof = found.filtered(lambda f: f.check_code == 'geo_ai_proof')
        self.assertEqual(proof.severity, 'warning')
        self.assertTrue(proof.suggested_fix)

        # Re-run with only one dimension flagged → the other auto-resolves.
        page._ai_geo_apply_issues([issues[0]])
        self.assertEqual(set(self._open(page).mapped('check_code')),
                         {'geo_ai_specificity'})

    def test_rerun_updates_existing_not_duplicate(self):
        page = self._make_page('geoai2')
        base = {'code': 'geo_ai_answer_summary', 'severity': 'info',
                'title': 'Add answer summary', 'detail': 'd',
                'recommendation': 'first'}
        page._ai_geo_apply_issues([base])
        page._ai_geo_apply_issues([dict(base, recommendation='second')])
        found = self._open(page)
        self.assertEqual(len(found), 1, 'Re-run must update, not duplicate.')
        self.assertEqual(found.suggested_fix, 'second')

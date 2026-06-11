"""Deleting a website.page must clean up its audit findings (no orphans)."""
from odoo.tests import tagged

from .common import EraSeoTestCase


@tagged('post_install', '-at_install')
class TestFindingCleanup(EraSeoTestCase):

    def test_findings_deleted_with_page(self):
        view = self.env['ir.ui.view'].create({
            'name': 'orphan test view', 'type': 'qweb',
            'arch': '<div>x</div>', 'key': 'era_seo_suite.orphan_test',
        })
        page = self.env['website.page'].create({
            'view_id': view.id, 'url': '/orphan-test'})
        finding = self.env['era.seo.audit.finding'].sudo().create({
            'check_code': 'slug_contains_stopwords',
            'check_name': 'Test', 'severity': 'info',
            'res_model': 'website.page', 'res_id': page.id,
            'url': page.url,
        })
        self.assertTrue(finding.exists())
        page.unlink()
        self.assertFalse(
            finding.exists(),
            'Audit finding should be removed when its target page is deleted.')

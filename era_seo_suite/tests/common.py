"""Shared test base for era_seo_suite.

Usage:
    from odoo.addons.era_seo_suite.tests.common import EraSeoTestCase

Every test that needs a website.page with SEO fields should subclass this.
"""
from odoo.tests import TransactionCase


class EraSeoTestCase(TransactionCase):
    """Base class providing a minimal website.page fixture with ERA SEO fields."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Minimal ir.ui.view required by website.page._inherits.
        cls.test_view = cls.env['ir.ui.view'].create({
            'name': 'ERA SEO Test View',
            'type': 'qweb',
            'arch': '<div>test content</div>',
            'key': 'era_seo_suite.test_view',
        })
        cls.test_page = cls.env['website.page'].create({
            'view_id': cls.test_view.id,
            'url': '/era-seo-test',
        })

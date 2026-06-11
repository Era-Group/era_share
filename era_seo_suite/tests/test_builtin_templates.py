"""Tests for all 17 built-in JSON-LD schema templates.

Verifies that every data-loaded template renders to valid JSON
against a minimal context.

Per SPEC §8 Step 5 / CLAUDE.md §6.
"""

import json

from odoo.tests import TransactionCase

from .common import EraSeoTestCase

# All 17 built-in template codes defined in seo_schema_template_data.xml.
BUILTIN_CODES = [
    'organization',
    'local_business',
    'website',
    'mobile_application',
    'software_application',
    'article',
    'blog_posting',
    'news_article',
    'faq_page',
    'how_to',
    'breadcrumb_list',
    'product',
    'service',
    'person',
    'event',
    'video_object',
    'aggregate_rating',
]


class TestBuiltinTemplates(EraSeoTestCase):
    """Every built-in template must render to valid JSON without errors."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Template = cls.env['era.seo.schema.template']
        from odoo.addons.era_seo_suite.models.seo_schema_engine import (
            build_context,
            render_jsonld,
        )
        cls.build_context = staticmethod(build_context)
        cls.render_jsonld = staticmethod(render_jsonld)

    def _render_template(self, code):
        """Render a template by code against the test page and return parsed JSON."""
        tpl = self.Template.search([('code', '=', code)], limit=1)
        self.assertTrue(
            tpl,
            'Built-in template with code=%r was not found in the database.' % code,
        )
        ctx = self.build_context(self.env, record=self.test_page)
        raw = self.render_jsonld(tpl.body, ctx, record=self.test_page)
        return json.loads(raw)

    def _make_test(code):
        """Factory for per-template test methods."""
        def test_method(self):
            parsed = self._render_template(code)
            self.assertIsInstance(parsed, dict, 'Expected a JSON object for %r' % code)
            self.assertIn('@type', parsed, '@type missing in %r output' % code)
            # @type must match the template's schema_type.
            tpl = self.Template.search([('code', '=', code)], limit=1)
            self.assertEqual(
                parsed['@type'], tpl.schema_type,
                '@type mismatch for template %r' % code,
            )
        test_method.__name__ = 'test_builtin_%s' % code
        test_method.__doc__ = 'Built-in template %r renders valid JSON-LD.' % code
        return test_method

    # Generate one test method per built-in template.
    for _code in BUILTIN_CODES:
        locals()['test_builtin_' + _code] = _make_test(_code)
    del _code, _make_test

    def test_all_builtin_codes_present(self):
        """All 17 built-in template codes must exist in the database."""
        for code in BUILTIN_CODES:
            tpl = self.Template.search([('code', '=', code)], limit=1)
            self.assertTrue(tpl, 'Missing built-in template: %r' % code)

    def test_organization_is_default(self):
        tpl = self.Template.search([('code', '=', 'organization')], limit=1)
        self.assertTrue(tpl.is_default)

    def test_website_is_default(self):
        tpl = self.Template.search([('code', '=', 'website')], limit=1)
        self.assertTrue(tpl.is_default)

    def test_non_default_templates(self):
        """Templates other than organization and website must not be is_default."""
        non_default_codes = [c for c in BUILTIN_CODES if c not in ('organization', 'website')]
        for code in non_default_codes:
            tpl = self.Template.search([('code', '=', code)], limit=1)
            self.assertFalse(tpl.is_default, 'Template %r should not be is_default' % code)

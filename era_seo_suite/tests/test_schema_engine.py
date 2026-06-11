"""Tests for the JSON-LD placeholder resolution engine.

Pure-Python tests that exercise seo_schema_engine without hitting
the ORM.  A minimal mock context dict replaces the full ORM objects
so these tests run fast and don't need a real database where possible.

Per SPEC §8 / CLAUDE.md §6.
"""

import json

from odoo.tests import TransactionCase


class TestEngineResolve(TransactionCase):
    """Tests for _resolve_path and _resolve_placeholder."""

    def setUp(self):
        super().setUp()
        from odoo.addons.era_seo_suite.models.seo_schema_engine import (
            _resolve_path,
            _resolve_placeholder,
            render_jsonld,
            build_context,
        )
        self._resolve_path = _resolve_path
        self._resolve_placeholder = _resolve_placeholder
        self.render_jsonld = render_jsonld
        self.build_context = build_context

    # --- _resolve_path --------------------------------------------------------

    def test_simple_key(self):
        ctx = {'name': 'Acme'}
        self.assertEqual(self._resolve_path('name', ctx), 'Acme')

    def test_dotted_path(self):
        class Obj:
            name = 'ERA'
        ctx = {'company': Obj()}
        self.assertEqual(self._resolve_path('company.name', ctx), 'ERA')

    def test_missing_top_key(self):
        self.assertIsNone(self._resolve_path('nonexistent', {}))

    def test_missing_nested_key(self):
        class Obj:
            pass
        ctx = {'company': Obj()}
        self.assertIsNone(self._resolve_path('company.missing_attr', ctx))

    def test_max_depth_exceeded(self):
        # 6 segments — should return None (depth > _MAX_DEPTH=5)
        self.assertIsNone(self._resolve_path('a.b.c.d.e.f', {'a': {}}))

    def test_dict_nested(self):
        ctx = {'meta': {'title': 'Hello'}}
        self.assertEqual(self._resolve_path('meta.title', ctx), 'Hello')

    def test_callable_bound_method_is_called(self):
        """Bound methods with no arguments are called automatically."""
        class Obj:
            def get_profiles(self):
                return ['https://example.com']
        ctx = {'company': Obj()}
        result = self._resolve_path('company.get_profiles', ctx)
        self.assertEqual(result, ['https://example.com'])

    # --- _resolve_placeholder -------------------------------------------------

    def test_plain_string(self):
        ctx = {'title': 'My Page'}
        result = self._resolve_placeholder('title', ctx)
        self.assertEqual(json.loads(result), 'My Page')

    def test_json_filter_list(self):
        ctx = {'items': ['a', 'b']}
        result = self._resolve_placeholder('items | json', ctx)
        self.assertEqual(json.loads(result), ['a', 'b'])

    def test_string_filter(self):
        ctx = {'count': 42}
        result = self._resolve_placeholder('count | string', ctx)
        self.assertEqual(json.loads(result), '42')

    def test_default_filter_used(self):
        ctx = {'title': ''}
        result = self._resolve_placeholder('title | default("fallback")', ctx)
        self.assertEqual(json.loads(result), 'fallback')

    def test_default_filter_not_used_when_truthy(self):
        ctx = {'title': 'Real Title'}
        result = self._resolve_placeholder('title | default("fallback")', ctx)
        self.assertEqual(json.loads(result), 'Real Title')

    def test_unresolvable_returns_null(self):
        result = self._resolve_placeholder('missing.path', {})
        self.assertEqual(result, 'null')

    def test_arithmetic_operator_raises(self):
        with self.assertRaises(ValueError):
            self._resolve_placeholder('a + b', {'a': 1, 'b': 2})

    def test_or_operator_raises(self):
        with self.assertRaises(ValueError):
            self._resolve_placeholder('a or b', {'a': 1})

    def test_unknown_filter_raises(self):
        with self.assertRaises(ValueError):
            self._resolve_placeholder('name | upper', {'name': 'test'})

    def test_invalid_path_chars_raises(self):
        with self.assertRaises(ValueError):
            self._resolve_placeholder('name[0]', {'name': 'test'})

    # --- render_jsonld --------------------------------------------------------

    def test_render_simple_template(self):
        body = '{"@type": "Thing", "name": {{ record.name }}}'
        ctx = {'record': type('R', (), {'name': 'ERA'})()}
        result = self.render_jsonld(body, ctx)
        parsed = json.loads(result)
        self.assertEqual(parsed['@type'], 'Thing')
        self.assertEqual(parsed['name'], 'ERA')

    def test_render_null_placeholder(self):
        body = '{"@type": "Thing", "name": {{ record.missing }}}'
        ctx = {'record': type('R', (), {})()}
        result = self.render_jsonld(body, ctx)
        parsed = json.loads(result)
        self.assertIsNone(parsed['name'])

    def test_instance_data_overrides_context(self):
        body = '{"name": {{ name }}}'
        ctx = {'name': 'Global'}
        result = self.render_jsonld(body, ctx, instance_data={'name': 'Override'})
        parsed = json.loads(result)
        self.assertEqual(parsed['name'], 'Override')

    def test_empty_body_returns_null(self):
        result = self.render_jsonld('', {})
        self.assertEqual(result, 'null')

    # --- build_context --------------------------------------------------------

    def test_build_context_returns_dict(self):
        ctx = self.build_context(self.env)
        self.assertIsInstance(ctx, dict)
        self.assertIn('settings', ctx)
        self.assertIn('company', ctx)

    def test_build_context_with_record(self):
        page = self.env['website.page'].search([], limit=1)
        if page:
            ctx = self.build_context(self.env, record=page)
            self.assertEqual(ctx['record'], page)

    # --- site_url -------------------------------------------------------------

    def test_site_url_in_context(self):
        """build_context always exposes a site_url key."""
        ctx = self.build_context(self.env)
        self.assertIn('site_url', ctx)
        self.assertIsInstance(ctx['site_url'], str)

    def test_site_url_strips_trailing_slash(self):
        """site_url must never carry a trailing slash so templates can append /path cleanly."""
        from odoo.addons.era_seo_suite.models.seo_schema_engine import _resolve_site_url

        ICP = self.env['ir.config_parameter'].sudo()
        old = ICP.get_param('web.base.url')
        try:
            ICP.set_param('web.base.url', 'https://example.com/')
            # Resolve with no website -> falls back to ICP.
            site_url = _resolve_site_url(ICP, None)
            self.assertEqual(site_url, 'https://example.com')
        finally:
            if old is not False and old is not None:
                ICP.set_param('web.base.url', old)

    def test_site_url_falls_back_to_web_base_url(self):
        """Empty website.domain must yield the ICP web.base.url."""
        from odoo.addons.era_seo_suite.models.seo_schema_engine import _resolve_site_url

        ICP = self.env['ir.config_parameter'].sudo()
        # Build a stand-in website with an empty domain.
        website = type('W', (), {'domain': ''})()
        old = ICP.get_param('web.base.url')
        try:
            ICP.set_param('web.base.url', 'https://fallback.test')
            self.assertEqual(_resolve_site_url(ICP, website), 'https://fallback.test')
        finally:
            if old is not False and old is not None:
                ICP.set_param('web.base.url', old)

    def test_site_url_upgrades_bare_host_to_https(self):
        """A bare host like 'example.com' is upgraded to https://example.com."""
        from odoo.addons.era_seo_suite.models.seo_schema_engine import _resolve_site_url

        ICP = self.env['ir.config_parameter'].sudo()
        website = type('W', (), {'domain': 'example.com'})()
        self.assertEqual(_resolve_site_url(ICP, website), 'https://example.com')

    def test_site_url_preserves_explicit_scheme(self):
        """Explicit http:// is preserved (e.g. local dev)."""
        from odoo.addons.era_seo_suite.models.seo_schema_engine import _resolve_site_url

        ICP = self.env['ir.config_parameter'].sudo()
        website = type('W', (), {'domain': 'http://localhost:8069'})()
        self.assertEqual(_resolve_site_url(ICP, website), 'http://localhost:8069')

    def test_site_url_used_in_template_render(self):
        """End-to-end: a template referencing {{ site_url }} resolves correctly."""
        import json

        ICP = self.env['ir.config_parameter'].sudo()
        old = ICP.get_param('web.base.url')
        try:
            ICP.set_param('web.base.url', 'https://render.test')
            body = '{"@type": "Thing", "url": "{{ site_url }}/path"}'
            ctx = self.build_context(self.env)
            result = self.render_jsonld(body, ctx)
            parsed = json.loads(result)
            # Either the configured website.domain OR our fallback wins.
            # Both must be absolute, neither '/'.
            self.assertTrue(parsed['url'].startswith('http'))
            self.assertTrue(parsed['url'].endswith('/path'))
            self.assertNotEqual(parsed['url'], '/path')
        finally:
            if old is not False and old is not None:
                ICP.set_param('web.base.url', old)

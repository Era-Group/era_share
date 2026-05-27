"""HTTP-level tests: verify <script type="application/ld+json"> appears in <head>.

Uses HttpCase to fetch a real page and assert on the rendered HTML.

Per SPEC §8 Step 6 / CLAUDE.md §6.
"""

import html as _html

from odoo.tests import HttpCase, tagged


@tagged('post_install', '-at_install')
class TestSchemaRendering(HttpCase):
    """Visit a page with an attached schema instance and assert on the output."""

    def setUp(self):
        super().setUp()
        self.env = self.env(user=self.env.ref('base.user_admin'))

    def _make_page_with_schema(self, seo_title='Rendering Test', url='/era-schema-render-test'):
        """Helper: create a page + Organisation template instance, return page."""
        # Use su=True for test setup: the admin user is not automatically in SEO groups.
        sudo_env = self.env(su=True)
        # Find or create an organization template.
        tpl = sudo_env['era.seo.schema.template'].search(
            [('code', '=', 'organization')], limit=1
        )
        if not tpl:
            tpl = sudo_env['era.seo.schema.template'].create({
                'name': 'Organization',
                'code': 'organization',
                'schema_type': 'Organization',
                'category': 'core',
                'body': '{"@type": "Organization", "name": {{ company.name | default("ERA") }}}',
            })

        view = sudo_env['ir.ui.view'].create({
            'name': 'ERA SEO Render Test View',
            'type': 'qweb',
            # Must call website.layout so the <head> (and schema injection) is rendered.
            'arch': (
                '<t t-name="era_seo_manager.render_test_view">'
                '<t t-call="website.layout">'
                '<div id="wrap"><div class="container">render test</div></div>'
                '</t>'
                '</t>'
            ),
            'key': 'era_seo_manager.render_test_view',
        })
        page = sudo_env['website.page'].create({
            'view_id': view.id,
            'url': url,
            'is_published': True,
            'website_published': True,
        })
        page.write({'seo_title': seo_title})

        sudo_env['era.seo.schema.instance'].create({
            'template_id': tpl.id,
            'res_model': 'website.page',
            'res_id': page.id,
            'active': True,
            'sequence': 10,
        })
        return page

    def test_schema_script_tag_present(self):
        """Page with an active schema instance must emit ld+json script tag."""
        # Ensure the schema engine is enabled.
        self.env['ir.config_parameter'].sudo().set_param(
            'era_seo.schema_engine_enabled', 'True'
        )
        self._make_page_with_schema(url='/era-schema-render-test-1')
        response = self.url_open('/era-schema-render-test-1')
        self.assertEqual(response.status_code, 200)
        # QWeb HTML-entity-encodes content inside script tags; unescape first.
        decoded = _html.unescape(response.text)
        self.assertIn('application/ld+json', response.text)
        self.assertIn('"@type"', decoded)
        self.assertIn('Organization', decoded)

    def test_schema_disabled_no_script_tag(self):
        """When schema engine is disabled, no ld+json tag should appear."""
        self.env['ir.config_parameter'].sudo().set_param(
            'era_seo.schema_engine_enabled', 'False'
        )
        self._make_page_with_schema(url='/era-schema-render-test-2')
        response = self.url_open('/era-schema-render-test-2')
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('application/ld+json', response.text)

    def test_site_schema_renders_on_homepage(self):
        """Site-level schemas (res_model='website') must render on the home page.

        The home page controller does not set main_object, so the old
        ``main_object and ...`` guard would short-circuit to False, emitting
        nothing. The dual-source renderer fixes this by fetching site schemas
        independently. Phase 2.1 regression test.
        """
        self.env['ir.config_parameter'].sudo().set_param(
            'era_seo.schema_engine_enabled', 'True'
        )
        sudo_env = self.env(su=True)
        website = sudo_env['website'].search([], limit=1)
        tpl = sudo_env['era.seo.schema.template'].search(
            [('code', '=', 'organization')], limit=1
        )
        if not tpl:
            tpl = sudo_env['era.seo.schema.template'].create({
                'name': 'Organization',
                'code': 'organization',
                'schema_type': 'Organization',
                'category': 'core',
                'body': '{"@type": "Organization", "name": {{ company.name | default("ERA") }}}',
            })

        # Attach at website level (not website.page).
        existing = sudo_env['era.seo.schema.instance'].search([
            ('res_model', '=', 'website'),
            ('res_id', '=', website.id),
            ('template_id', '=', tpl.id),
        ], limit=1)
        if not existing:
            sudo_env['era.seo.schema.instance'].create({
                'template_id': tpl.id,
                'res_model': 'website',
                'res_id': website.id,
                'sequence': 10,
                'active': True,
            })

        response = self.url_open('/')
        self.assertEqual(response.status_code, 200)
        decoded = _html.unescape(response.text)
        self.assertIn('application/ld+json', response.text,
                      'Site-level schema must render even when main_object is None.')
        self.assertIn('Organization', decoded)

    def test_inactive_instance_not_rendered(self):
        """Inactive schema instances must not appear in the page output."""
        self.env['ir.config_parameter'].sudo().set_param(
            'era_seo.schema_engine_enabled', 'True'
        )
        page = self._make_page_with_schema(url='/era-schema-render-test-3')
        # Deactivate the instance.
        instances = self.env(su=True)['era.seo.schema.instance'].search([
            ('res_model', '=', 'website.page'),
            ('res_id', '=', page.id),
        ])
        instances.write({'active': False})

        response = self.url_open('/era-schema-render-test-3')
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('application/ld+json', response.text)

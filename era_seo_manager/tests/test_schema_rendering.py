"""HTTP-level tests: verify <script type="application/ld+json"> appears in <head>.

Uses HttpCase to fetch a real page and assert on the rendered HTML.

Per SPEC §8 Step 6 / CLAUDE.md §6.
"""

from odoo.tests import HttpCase, tagged


@tagged('post_install', '-at_install')
class TestSchemaRendering(HttpCase):
    """Visit a page with an attached schema instance and assert on the output."""

    def setUp(self):
        super().setUp()
        self.env = self.env(user=self.env.ref('base.user_admin'))

    def _make_page_with_schema(self, seo_title='Rendering Test', url='/era-schema-render-test'):
        """Helper: create a page + Organisation template instance, return page."""
        # Use sudo() for test setup: the admin user is not automatically in SEO groups.
        sudo_env = self.env.sudo()
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
            'arch': '<t t-name="era_seo_manager.render_test"><div>render test</div></t>',
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
        self.assertIn('application/ld+json', response.text)
        self.assertIn('"@type"', response.text)
        self.assertIn('Organization', response.text)

    def test_schema_disabled_no_script_tag(self):
        """When schema engine is disabled, no ld+json tag should appear."""
        self.env['ir.config_parameter'].sudo().set_param(
            'era_seo.schema_engine_enabled', 'False'
        )
        self._make_page_with_schema(url='/era-schema-render-test-2')
        response = self.url_open('/era-schema-render-test-2')
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('application/ld+json', response.text)

    def test_inactive_instance_not_rendered(self):
        """Inactive schema instances must not appear in the page output."""
        self.env['ir.config_parameter'].sudo().set_param(
            'era_seo.schema_engine_enabled', 'True'
        )
        page = self._make_page_with_schema(url='/era-schema-render-test-3')
        # Deactivate the instance.
        instances = self.env.sudo()['era.seo.schema.instance'].search([
            ('res_model', '=', 'website.page'),
            ('res_id', '=', page.id),
        ])
        instances.write({'active': False})

        response = self.url_open('/era-schema-render-test-3')
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('application/ld+json', response.text)

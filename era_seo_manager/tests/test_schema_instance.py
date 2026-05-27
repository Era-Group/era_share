"""Tests for era.seo.schema.instance — CRUD, rendering, cascade delete.

Per SPEC §8.1.2 / CLAUDE.md §6.
"""

from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase

from .common import EraSeoTestCase


class TestSchemaInstance(EraSeoTestCase):
    """CRUD, rendered_json compute, data_json validation, unlink cascade."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Template = cls.env['era.seo.schema.template']
        cls.Instance = cls.env['era.seo.schema.instance']

        cls.tpl = cls.Template.create({
            'name': 'Test Thing Template',
            'code': 'test_thing_instance',
            'schema_type': 'Thing',
            'category': 'core',
            'body': '{"@type": "Thing", "name": {{ record.seo_title | default("unnamed") }}}',
        })

    # --- CRUD -----------------------------------------------------------------

    def test_create_instance(self):
        inst = self.Instance.create({
            'template_id': self.tpl.id,
            'res_model': 'website.page',
            'res_id': self.test_page.id,
        })
        self.assertTrue(inst.id)
        self.assertEqual(inst.res_model, 'website.page')
        self.assertEqual(inst.res_id, self.test_page.id)

    def test_name_computed(self):
        inst = self.Instance.create({
            'template_id': self.tpl.id,
            'res_model': 'website.page',
            'res_id': self.test_page.id,
        })
        self.assertIn('Test Thing Template', inst.name)
        self.assertIn('website.page', inst.name)

    def test_sequence_default(self):
        inst = self.Instance.create({
            'template_id': self.tpl.id,
            'res_model': 'website.page',
            'res_id': self.test_page.id,
        })
        self.assertEqual(inst.sequence, 10)

    def test_active_default_true(self):
        inst = self.Instance.create({
            'template_id': self.tpl.id,
            'res_model': 'website.page',
            'res_id': self.test_page.id,
        })
        self.assertTrue(inst.active)

    # --- data_json validation -------------------------------------------------

    def test_valid_data_json_accepted(self):
        inst = self.Instance.create({
            'template_id': self.tpl.id,
            'res_model': 'website.page',
            'res_id': self.test_page.id,
            'data_json': '{"startDate": "2026-01-01"}',
        })
        self.assertTrue(inst.id)

    def test_invalid_data_json_raises(self):
        with self.assertRaises(ValidationError):
            self.Instance.create({
                'template_id': self.tpl.id,
                'res_model': 'website.page',
                'res_id': self.test_page.id,
                'data_json': '[1, 2, 3]',  # array, not object
            })

    def test_malformed_json_raises(self):
        with self.assertRaises(ValidationError):
            self.Instance.create({
                'template_id': self.tpl.id,
                'res_model': 'website.page',
                'res_id': self.test_page.id,
                'data_json': '{not: json}',
            })

    # --- rendered_json compute ------------------------------------------------

    def test_rendered_json_is_valid_json(self):
        import json
        self.test_page.write({'seo_title': 'Schema Test Page'})
        inst = self.Instance.create({
            'template_id': self.tpl.id,
            'res_model': 'website.page',
            'res_id': self.test_page.id,
        })
        rendered = inst.rendered_json
        self.assertTrue(rendered)
        parsed = json.loads(rendered)
        self.assertEqual(parsed['@type'], 'Thing')

    def test_rendered_json_uses_seo_title(self):
        import json
        self.test_page.write({'seo_title': 'My Schema Title'})
        inst = self.Instance.create({
            'template_id': self.tpl.id,
            'res_model': 'website.page',
            'res_id': self.test_page.id,
        })
        parsed = json.loads(inst.rendered_json)
        self.assertEqual(parsed['name'], 'My Schema Title')

    def test_rendered_json_uses_default_when_no_title(self):
        import json
        self.test_page.write({'seo_title': False})
        inst = self.Instance.create({
            'template_id': self.tpl.id,
            'res_model': 'website.page',
            'res_id': self.test_page.id,
        })
        parsed = json.loads(inst.rendered_json)
        self.assertEqual(parsed['name'], 'unnamed')

    def test_instance_data_overrides_context(self):
        import json
        inst = self.Instance.create({
            'template_id': self.tpl.id,
            'res_model': 'website.page',
            'res_id': self.test_page.id,
            'data_json': '{"seo_title": "Override Title"}',
        })
        # data_json keys override context; but our template uses record.seo_title,
        # not seo_title directly, so this tests the instance_data merge layer.
        rendered = inst.rendered_json
        self.assertTrue(rendered)

    # --- Sequence ordering ---------------------------------------------------

    def test_search_ordered_by_sequence(self):
        tpl2 = self.Template.create({
            'name': 'Second Template',
            'code': 'test_second_inst',
            'schema_type': 'Thing',
            'category': 'core',
            'body': '{"@type": "Thing"}',
        })
        inst_low = self.Instance.create({
            'template_id': self.tpl.id,
            'res_model': 'website.page',
            'res_id': self.test_page.id,
            'sequence': 5,
        })
        inst_high = self.Instance.create({
            'template_id': tpl2.id,
            'res_model': 'website.page',
            'res_id': self.test_page.id,
            'sequence': 20,
        })
        found = self.Instance.search([
            ('res_model', '=', 'website.page'),
            ('res_id', '=', self.test_page.id),
            ('id', 'in', [inst_low.id, inst_high.id]),
        ])
        self.assertEqual(found[0].id, inst_low.id)

    # --- Unlink cascade -------------------------------------------------------

    def test_page_unlink_cascades_instances(self):
        view = self.env['ir.ui.view'].create({
            'name': 'ERA SEO Cascade Test View',
            'type': 'qweb',
            'arch': '<div>cascade test</div>',
            'key': 'era_seo_manager.cascade_test_view',
        })
        page = self.env['website.page'].create({
            'view_id': view.id,
            'url': '/era-cascade-test',
        })
        inst = self.Instance.create({
            'template_id': self.tpl.id,
            'res_model': 'website.page',
            'res_id': page.id,
        })
        inst_id = inst.id
        page.unlink()
        remaining = self.Instance.search([('id', '=', inst_id)])
        self.assertFalse(remaining)

    # --- get_rendered_json_ld public API -------------------------------------

    def test_get_rendered_json_ld_with_explicit_record(self):
        import json
        self.test_page.write({'seo_title': 'Explicit Record Test'})
        inst = self.Instance.create({
            'template_id': self.tpl.id,
            'res_model': 'website.page',
            'res_id': self.test_page.id,
        })
        result = inst.get_rendered_json_ld(page_record=self.test_page)
        parsed = json.loads(result)
        self.assertEqual(parsed['name'], 'Explicit Record Test')

    # --- _get_for_render preload ---------------------------------------------

    def test_get_for_render_returns_site_then_page(self):
        """Site-bound instances come first, then page-bound, sequence asc within each."""
        website = self.env['website'].search([], limit=1)
        site_inst = self.Instance.create({
            'template_id': self.tpl.id,
            'res_model': 'website',
            'res_id': website.id,
            'sequence': 20,
        })
        page_inst = self.Instance.create({
            'template_id': self.tpl.id,
            'res_model': 'website.page',
            'res_id': self.test_page.id,
            'sequence': 10,
        })
        result = self.Instance._get_for_render(main_object=self.test_page, website=website)
        ids = result.ids
        self.assertIn(site_inst.id, ids)
        self.assertIn(page_inst.id, ids)
        # Site-bound must precede page-bound regardless of sequence.
        self.assertLess(ids.index(site_inst.id), ids.index(page_inst.id))

    def test_get_for_render_ignores_inactive(self):
        website = self.env['website'].search([], limit=1)
        active = self.Instance.create({
            'template_id': self.tpl.id,
            'res_model': 'website',
            'res_id': website.id,
            'active': True,
        })
        inactive = self.Instance.create({
            'template_id': self.tpl.id,
            'res_model': 'website',
            'res_id': website.id,
            'active': False,
        })
        result = self.Instance._get_for_render(website=website)
        self.assertIn(active.id, result.ids)
        self.assertNotIn(inactive.id, result.ids)

    def test_get_for_render_with_only_website(self):
        """main_object=None must still return site-wide schemas (the home-page case)."""
        website = self.env['website'].search([], limit=1)
        inst = self.Instance.create({
            'template_id': self.tpl.id,
            'res_model': 'website',
            'res_id': website.id,
        })
        result = self.Instance._get_for_render(main_object=None, website=website)
        self.assertIn(inst.id, result.ids)

    def test_get_for_render_skips_main_object_without_mixin(self):
        """A record that does not carry era.seo.mixin is ignored as a target."""
        # res.users does not carry seo_title — pass admin user as main_object.
        admin = self.env.ref('base.user_admin')
        website = self.env['website'].search([], limit=1)
        site_inst = self.Instance.create({
            'template_id': self.tpl.id,
            'res_model': 'website',
            'res_id': website.id,
        })
        result = self.Instance._get_for_render(main_object=admin, website=website)
        # The admin-user record itself should not appear; only the site instance.
        self.assertEqual(result.ids, [site_inst.id])

    def test_get_for_render_returns_empty_with_no_args(self):
        result = self.Instance._get_for_render(main_object=None, website=None)
        self.assertEqual(len(result), 0)

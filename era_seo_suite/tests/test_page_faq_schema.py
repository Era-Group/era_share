"""Tests for the auto-derived FAQPage schema on website.page.

Covers content extraction (incl. excluding non-FAQ cards), auto-attach on
create, removal when the FAQ content goes away (via an ir.ui.view edit), and
live JSON-LD rendering through the schema engine.
"""
import json

from odoo.tests import tagged

from .common import EraSeoTestCase


_FAQ_ARCH = """
<div>
  <section class="s_faq_collapse">
    <div class="accordion" id="faq1">
      <div class="card">
        <a class="card-header" role="button">What is Odoo?</a>
        <div class="collapse"><div class="card-body"><p>Odoo is an open-source ERP suite.</p></div></div>
      </div>
      <div class="card">
        <a class="card-header" role="button">Do you support Saudi e-invoicing?</a>
        <div class="collapse"><div class="card-body"><p>Yes, ZATCA phase 2 is supported.</p></div></div>
      </div>
    </div>
  </section>
  <div class="card"><div class="card-body">A pricing card, not an FAQ.</div></div>
</div>
"""

_NO_FAQ_ARCH = '<div><section class="s_text_block"><p>No FAQ here.</p></section></div>'


# Runs post_install so the registry is ready: the ir.ui.view content-change
# hook (which re-syncs the FAQ schema) is intentionally a no-op while the
# registry is still loading, so the removal path can only be exercised at
# runtime — which is what post_install reproduces.
@tagged('post_install', '-at_install')
class TestPageFaqSchema(EraSeoTestCase):

    def _faq_instances(self, page):
        return self.env['era.seo.schema.instance'].sudo().search([
            ('res_model', '=', 'website.page'),
            ('res_id', '=', page.id),
            ('template_id.code', '=', 'page_faq_page'),
        ])

    def _make_page(self, arch, slug):
        view = self.env['ir.ui.view'].create({
            'name': 'FAQ test view %s' % slug,
            'type': 'qweb',
            'arch': arch,
            'key': 'era_seo_suite.faq_test_%s' % slug,
        })
        return self.env['website.page'].create({
            'view_id': view.id,
            'url': '/%s' % slug,
        })

    def test_extracts_qa_pairs_and_excludes_non_faq_cards(self):
        page = self._make_page(_FAQ_ARCH, 'faq_extract')
        entities = page.era_faq_main_entity()
        self.assertEqual(len(entities), 2)
        self.assertEqual(entities[0]['name'], 'What is Odoo?')
        self.assertEqual(entities[0]['@type'], 'Question')
        self.assertEqual(
            entities[0]['acceptedAnswer']['text'],
            'Odoo is an open-source ERP suite.',
        )
        # The standalone pricing .card-body must NOT be picked up as an answer.
        names = [e['name'] for e in entities]
        self.assertNotIn('A pricing card, not an FAQ.', names)

    def test_instance_attached_when_page_has_faq(self):
        page = self._make_page(_FAQ_ARCH, 'faq_attach')
        self.assertTrue(
            self._faq_instances(page),
            'FAQPage instance should auto-attach on create.',
        )

    def test_no_instance_when_page_has_no_faq(self):
        page = self._make_page(_NO_FAQ_ARCH, 'faq_none')
        self.assertFalse(page.era_faq_main_entity())
        self.assertFalse(self._faq_instances(page))

    def test_rendered_jsonld_contains_questions(self):
        page = self._make_page(_FAQ_ARCH, 'faq_render')
        inst = self._faq_instances(page)
        self.assertTrue(inst)
        parsed = json.loads(inst.get_rendered_json_ld(page))
        self.assertEqual(parsed['@type'], 'FAQPage')
        self.assertEqual(len(parsed['mainEntity']), 2)
        self.assertEqual(parsed['mainEntity'][0]['@type'], 'Question')
        self.assertEqual(
            parsed['mainEntity'][1]['acceptedAnswer']['text'],
            'Yes, ZATCA phase 2 is supported.',
        )

    def test_instance_removed_when_faq_content_removed(self):
        page = self._make_page(_FAQ_ARCH, 'faq_remove')
        self.assertTrue(self._faq_instances(page))
        # Editing the underlying view arch (the editor-save path) must drop it.
        page.view_id.write({'arch': _NO_FAQ_ARCH})
        self.assertFalse(
            self._faq_instances(page),
            'FAQPage instance should be removed when the FAQ content goes away.',
        )

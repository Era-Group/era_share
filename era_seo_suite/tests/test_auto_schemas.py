"""Auto-attach of BlogPosting / BreadcrumbList / FAQPage on blog.post."""
import json

from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestAutoSchemas(TransactionCase):
    """Schema instances should appear automatically on blog.post create/write."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.BlogPost = cls.env['blog.post']
        cls.Instance = cls.env['era.seo.schema.instance']
        cls.Blog = cls.env['blog.blog'].search([], limit=1) or \
                   cls.env['blog.blog'].create({'name': 'Auto-schema test blog'})

    def _make_post(self, **vals):
        defaults = {
            'name': 'Schema test post',
            'blog_id': self.Blog.id,
            'content': '<p>Some content.</p>',
        }
        defaults.update(vals)
        return self.BlogPost.create(defaults)

    def _instances_for(self, post, code):
        return self.Instance.search([
            ('res_model', '=', 'blog.post'),
            ('res_id', '=', post.id),
            ('template_id.code', '=', code),
        ])

    def test_blog_posting_attached_on_create(self):
        post = self._make_post()
        self.assertTrue(self._instances_for(post, 'blog_posting'),
                        'BlogPosting should auto-attach on create.')

    def test_breadcrumb_list_attached_on_create(self):
        post = self._make_post()
        self.assertTrue(self._instances_for(post, 'breadcrumb_list'),
                        'BreadcrumbList should auto-attach on create.')

    def test_faq_page_NOT_attached_when_no_faqs(self):
        post = self._make_post()
        self.assertFalse(self._instances_for(post, 'blog_faq_page'))

    def test_faq_page_attached_when_faq_created(self):
        post = self._make_post()
        self.env['era.blog.faq'].create({
            'post_id': post.id,
            'question': 'Why?',
            'answer': '<p>Because.</p>',
        })
        post.invalidate_recordset()
        self.assertTrue(self._instances_for(post, 'blog_faq_page'),
                        'FAQPage should auto-attach when first FAQ is created.')

    def test_faq_page_data_json_carries_questions(self):
        post = self._make_post()
        self.env['era.blog.faq'].create({
            'post_id': post.id,
            'question': 'How?',
            'answer': '<p>Like so.</p>',
        })
        post.invalidate_recordset()
        inst = self._instances_for(post, 'blog_faq_page')
        parsed = json.loads(inst.data_json)
        self.assertIn('faq_main_entity', parsed)
        self.assertEqual(parsed['faq_main_entity'][0]['name'], 'How?')
        self.assertIn('Like so', parsed['faq_main_entity'][0]['acceptedAnswer']['text'])

    def test_faq_page_removed_when_last_faq_deleted(self):
        post = self._make_post()
        faq = self.env['era.blog.faq'].create({
            'post_id': post.id,
            'question': 'Q?',
            'answer': '<p>A.</p>',
        })
        self.assertTrue(self._instances_for(post, 'blog_faq_page'))
        faq.unlink()
        self.assertFalse(self._instances_for(post, 'blog_faq_page'),
                         'FAQPage should be removed when last FAQ is deleted.')

    def test_idempotent_resync(self):
        """Calling _sync_era_default_schemas() twice does not create duplicates."""
        post = self._make_post()
        post._sync_era_default_schemas()
        post._sync_era_default_schemas()
        self.assertEqual(len(self._instances_for(post, 'blog_posting')), 1)
        self.assertEqual(len(self._instances_for(post, 'breadcrumb_list')), 1)

"""Tests for era_seo_blog — blog.post extensions and supporting models."""
import logging

from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged

_logger = logging.getLogger(__name__)


@tagged('post_install', '-at_install')
class TestBlogPostExtensions(TransactionCase):
    """Reading time, word count, excerpt, TOC, related posts."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.BlogPost = cls.env['blog.post']
        cls.Blog = cls.env['blog.blog'].search([], limit=1)
        if not cls.Blog:
            cls.Blog = cls.env['blog.blog'].create({'name': 'ERA test blog'})

    def _make_post(self, name='Post', content='<p>Hello world.</p>', **extra):
        vals = {
            'name': name,
            'blog_id': self.Blog.id,
            'content': content,
        }
        vals.update(extra)
        return self.BlogPost.create(vals)

    # --- Word count + reading time -------------------------------------------

    def test_reading_time_min_one(self):
        """Any non-empty post should report at least 1 minute."""
        post = self._make_post(content='<p>Hello.</p>')
        self.assertGreaterEqual(post.era_reading_time_minutes, 1)

    def test_reading_time_zero_when_empty(self):
        post = self._make_post(content='')
        self.assertEqual(post.era_reading_time_minutes, 0)
        self.assertEqual(post.era_word_count, 0)

    def test_word_count_strips_html(self):
        """HTML tags don't inflate the word count."""
        content = '<p>One</p><p>Two</p><p>Three</p>'
        post = self._make_post(content=content)
        self.assertEqual(post.era_word_count, 3)

    def test_reading_time_long_post(self):
        """600 words at 200 wpm → 3 minutes."""
        words = ' '.join(['word'] * 600)
        post = self._make_post(content=f'<p>{words}</p>')
        self.assertEqual(post.era_word_count, 600)
        self.assertEqual(post.era_reading_time_minutes, 3)

    def test_word_count_uses_richest_language(self):
        """If the source language is a stub, count the richest translation.

        Regression: the English source often stays 'Start writing here...'
        (3 words) while the real article is a translation; the count must
        reflect the language that actually carries the content.
        """
        self.env.ref('base.lang_fr').active = True
        post = self._make_post(content='<p>one two three</p>')  # en: 3 words
        self.assertEqual(post.era_word_count, 3)
        rich = '<p>%s</p>' % ' '.join(['mot'] * 50)
        post.with_context(lang='fr_FR').write({'content': rich})
        post.invalidate_recordset()
        self.assertEqual(post.era_word_count, 50)

    # --- Excerpt fallback ----------------------------------------------------

    def test_excerpt_uses_explicit_value(self):
        post = self._make_post(era_excerpt='Custom excerpt')
        self.assertEqual(post.era_excerpt_effective, 'Custom excerpt')

    def test_excerpt_falls_back_to_content(self):
        post = self._make_post(
            content='<p>The first 200 characters of the post content '
                    'should appear when no explicit excerpt is set.</p>'
        )
        self.assertIn('first 200 characters', post.era_excerpt_effective)

    def test_excerpt_truncates_with_ellipsis(self):
        long_text = 'A' * 250
        post = self._make_post(content=f'<p>{long_text}</p>')
        self.assertTrue(post.era_excerpt_effective.endswith('…'))
        self.assertEqual(len(post.era_excerpt_effective), 201)

    # --- TOC -----------------------------------------------------------------

    def test_toc_from_h2_only(self):
        content = '<p>Intro</p><h2>First</h2><p>x</p><h2>Second</h2>'
        post = self._make_post(content=content)
        self.assertIn('First', post.era_toc_html)
        self.assertIn('Second', post.era_toc_html)
        self.assertIn('href="#first"', post.era_toc_html)

    def test_toc_empty_without_headings(self):
        post = self._make_post(content='<p>No headings here.</p>')
        self.assertFalse(post.era_toc_html or '')

    def test_toc_h2_then_h3_nested(self):
        content = '<h2>Outer</h2><h3>Inner</h3>'
        post = self._make_post(content=content)
        self.assertIn('Outer', post.era_toc_html)
        self.assertIn('Inner', post.era_toc_html)

    # --- Series neighbors ----------------------------------------------------

    def test_series_neighbors(self):
        series = self.env['era.blog.series'].create({
            'name': 'My Series',
            'slug': 'my-series',
        })
        p1 = self._make_post(name='P1', era_series_id=series.id, era_series_sequence=10)
        p2 = self._make_post(name='P2', era_series_id=series.id, era_series_sequence=20)
        p3 = self._make_post(name='P3', era_series_id=series.id, era_series_sequence=30)
        # Force recompute by invalidating cache.
        (p1 | p2 | p3).invalidate_recordset()
        self.assertFalse(p1.era_series_prev_id)
        self.assertEqual(p1.era_series_next_id, p2)
        self.assertEqual(p2.era_series_prev_id, p1)
        self.assertEqual(p2.era_series_next_id, p3)
        self.assertEqual(p3.era_series_prev_id, p2)
        self.assertFalse(p3.era_series_next_id)

    # --- Canonical override --------------------------------------------------

    def test_canonical_external_url_wins(self):
        post = self._make_post(
            seo_title='Syndicated',
            era_canonical_external_url='https://other.example.com/original',
        )
        meta = post.get_seo_meta_dict()
        self.assertEqual(meta['canonical'], 'https://other.example.com/original')

    # --- og:type forced to article -------------------------------------------

    def test_og_type_article(self):
        post = self._make_post(seo_title='Article test')
        meta = post.get_seo_meta_dict()
        self.assertEqual(meta['og_type'], 'article')


@tagged('post_install', '-at_install')
class TestBlogSeries(TransactionCase):

    def test_slug_required(self):
        Series = self.env['era.blog.series']
        with self.assertRaises(ValidationError):
            Series.create({'name': 'Bad', 'slug': 'Has Caps and Spaces'})

    def test_slug_unique(self):
        Series = self.env['era.blog.series']
        Series.create({'name': 'A', 'slug': 'unique-slug-test'})
        from psycopg2 import IntegrityError
        from odoo.exceptions import ValidationError as VE
        with self.assertRaises((IntegrityError, VE)):
            with self.env.cr.savepoint():
                Series.create({'name': 'B', 'slug': 'unique-slug-test'})

    def test_seo_path(self):
        s = self.env['era.blog.series'].create({'name': 'Foo', 'slug': 'foo-series'})
        self.assertEqual(s._get_seo_path(), '/blog/series/foo-series')


@tagged('post_install', '-at_install')
class TestBlogCategory(TransactionCase):

    def test_hierarchy(self):
        Cat = self.env['era.blog.category']
        parent = Cat.create({'name': 'Engineering', 'slug': 'engineering'})
        child = Cat.create({'name': 'Backend', 'slug': 'backend', 'parent_id': parent.id})
        self.assertIn(child, parent.child_ids)
        self.assertEqual(child.parent_id, parent)

    def test_cycle_rejected(self):
        Cat = self.env['era.blog.category']
        parent = Cat.create({'name': 'Top', 'slug': 'top-cat'})
        child = Cat.create({'name': 'Sub', 'slug': 'sub-cat', 'parent_id': parent.id})
        with self.assertRaises(ValidationError):
            parent.parent_id = child


@tagged('post_install', '-at_install')
class TestBlogAuthor(TransactionCase):

    def test_seo_path(self):
        a = self.env['era.blog.author'].create({
            'name': 'Jane Doe',
            'slug': 'jane-doe',
        })
        self.assertEqual(a._get_seo_path(), '/blog/author/jane-doe')

    def test_user_link_pulls_email(self):
        user = self.env.ref('base.user_admin')
        a = self.env['era.blog.author'].new({'name': 'Linked', 'slug': 'linked',
                                              'user_id': user.id})
        a._onchange_user_id()
        if user.email:
            self.assertEqual(a.email, user.email)

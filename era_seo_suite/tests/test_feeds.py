"""HTTP tests for the RSS / Atom / JSON feed controllers."""
import json

from odoo.tests import HttpCase, tagged


@tagged('post_install', '-at_install')
class TestFeedControllers(HttpCase):
    """Hit the feed routes and verify content type + minimum structure."""

    def setUp(self):
        super().setUp()
        Blog = self.env['blog.blog'].search([], limit=1) or \
               self.env['blog.blog'].create({'name': 'Feed test blog'})
        self.env['blog.post'].create({
            'name': 'Feed-test post',
            'blog_id': Blog.id,
            'content': '<p>Hello feed reader.</p>',
            'is_published': True,
        })

    def test_rss_feed_200_and_xml(self):
        response = self.url_open('/blog/feed/rss')
        self.assertEqual(response.status_code, 200)
        self.assertIn('application/rss+xml', response.headers.get('Content-Type', ''))
        self.assertIn('<rss', response.text)
        self.assertIn('Feed-test post', response.text)

    def test_atom_feed_200_and_xml(self):
        response = self.url_open('/blog/feed/atom')
        self.assertEqual(response.status_code, 200)
        self.assertIn('application/atom+xml', response.headers.get('Content-Type', ''))
        self.assertIn('<feed', response.text)

    def test_json_feed_200_and_parses(self):
        response = self.url_open('/blog/feed/json')
        self.assertEqual(response.status_code, 200)
        self.assertIn('application/feed+json', response.headers.get('Content-Type', ''))
        data = json.loads(response.text)
        self.assertEqual(data['version'], 'https://jsonfeed.org/version/1.1')
        self.assertIsInstance(data['items'], list)
        self.assertTrue(any('Feed-test post' in item['title'] for item in data['items']))

    def test_feed_cache_control_header(self):
        response = self.url_open('/blog/feed/rss')
        cache = response.headers.get('Cache-Control', '')
        self.assertIn('max-age=', cache)

    def test_unknown_format_404s(self):
        response = self.url_open('/blog/tag/anything/feed/yaml')
        self.assertEqual(response.status_code, 404)

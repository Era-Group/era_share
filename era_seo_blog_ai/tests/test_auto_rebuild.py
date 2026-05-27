"""Tests for the blog-post AI SEO auto-rebuild on content change.

The ``ai.agent`` is mocked via ``AIClient._resolve_agent`` so no LLM
provider / network is needed.
"""
from unittest.mock import MagicMock, patch

from odoo.tests import TransactionCase, tagged

from odoo.addons.era_seo_ai.models.ai_client import AIClient


def _mock_agent(reply_json):
    agent = MagicMock()
    agent.name = 'Test SEO Agent'
    agent.llm_model = 'gpt-test'
    agent.get_direct_response.return_value = [reply_json]
    return agent


_FULL_REPLY = (
    '{"seo_title": "AI Generated Title", '
    '"seo_description": "An AI generated meta description for the blog post body.", '
    '"seo_og_title": "AI OG Title", '
    '"seo_og_description": "AI OG description.", '
    '"seo_keywords": "blog, seo, ai", '
    '"explanation": "Derived from the post body.", "confidence": 0.9}'
)

# Reply that also carries the blog-specific fields the bridge adds.
_FULL_REPLY_BLOG = (
    '{"seo_title": "AI Generated Title", '
    '"seo_description": "An AI generated meta description.", '
    '"seo_og_title": "AI OG Title", '
    '"seo_og_description": "AI OG description.", '
    '"seo_keywords": "blog, seo, ai", '
    '"era_subtitle": "An AI written subtitle", '
    '"era_excerpt": "An AI written excerpt for feeds and list cards.", '
    '"explanation": "Derived from the post body.", "confidence": 0.9}'
)


@tagged('post_install', '-at_install')
class TestBlogAutoRebuild(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ICP = cls.env['ir.config_parameter'].sudo()
        cls.ICP.set_param('era_seo.ai_enabled', 'True')
        cls.blog = cls.env['blog.blog'].create({'name': 'Test Blog'})

    # A body with >= 50 words of visible text (8 words x 8 = 64), so the
    # auto-rebuild fires.
    LONG_BODY = '<p>' + ('Cloud accounting and ZATCA compliance for Saudi SMEs. '
                         * 8) + '</p>'

    def _make_post(self, content='<p>Original body content.</p>'):
        # create() does not trigger the rebuild (only content *edits* do),
        # so the post starts with empty SEO regardless of the enabled flag.
        return self.env['blog.post'].create({
            'name': 'Test Post',
            'blog_id': self.blog.id,
            'content': content,
        })

    def test_content_edit_rewrites_seo(self):
        post = self._make_post()
        post.write({'seo_title': 'Hand-written'})
        with patch.object(AIClient, '_resolve_agent',
                          return_value=_mock_agent(_FULL_REPLY)):
            post.write({'content': self.LONG_BODY})
        post.invalidate_recordset()
        # Rewrite overwrites the hand-written value.
        self.assertEqual(post.seo_title, 'AI Generated Title')
        self.assertTrue(post.seo_description)
        self.assertEqual(post.seo_keywords, 'blog, seo, ai')

    def test_thin_content_skips_rebuild(self):
        post = self._make_post()
        agent = _mock_agent(_FULL_REPLY)
        with patch.object(AIClient, '_resolve_agent', return_value=agent):
            # < 50 words of text -> no AI call, no SEO written.
            post.write({'content': '<p>Too short to bother running the AI.</p>'})
        post.invalidate_recordset()
        self.assertFalse(post.seo_title)
        agent.get_direct_response.assert_not_called()

    def test_no_rebuild_when_ai_disabled(self):
        self.ICP.set_param('era_seo.ai_enabled', 'False')
        post = self._make_post()
        agent = _mock_agent(_FULL_REPLY)
        with patch.object(AIClient, '_resolve_agent', return_value=agent):
            post.write({'content': self.LONG_BODY})
        post.invalidate_recordset()
        self.assertFalse(post.seo_title)
        agent.get_direct_response.assert_not_called()
        self.ICP.set_param('era_seo.ai_enabled', 'True')

    def test_non_content_write_does_not_rebuild(self):
        post = self._make_post()
        agent = _mock_agent(_FULL_REPLY)
        with patch.object(AIClient, '_resolve_agent', return_value=agent):
            post.write({'name': 'Renamed Post'})
        post.invalidate_recordset()
        self.assertFalse(post.seo_title)
        agent.get_direct_response.assert_not_called()

    def test_failed_ai_does_not_block_save(self):
        post = self._make_post()
        agent = _mock_agent('not valid json at all')
        with patch.object(AIClient, '_resolve_agent', return_value=agent):
            # Must not raise — the content save has to succeed regardless.
            post.write({'content': self.LONG_BODY})
        post.invalidate_recordset()
        self.assertEqual(post.content, self.LONG_BODY)
        self.assertFalse(post.seo_title)

    def test_fill_fields_include_blog_fields(self):
        post = self._make_post()
        names = [s['name'] for s in post._ai_fill_fields()]
        self.assertIn('era_subtitle', names)
        self.assertIn('era_excerpt', names)
        # Core fields still present.
        self.assertIn('seo_title', names)
        self.assertIn('seo_keywords', names)

    def test_rewrite_fills_blog_specific_fields(self):
        post = self._make_post()
        with patch.object(AIClient, '_resolve_agent',
                          return_value=_mock_agent(_FULL_REPLY_BLOG)):
            post.action_ai_rewrite_seo()
        post.invalidate_recordset()
        self.assertEqual(post.era_subtitle, 'An AI written subtitle')
        self.assertTrue(post.era_excerpt)
        self.assertEqual(post.seo_title, 'AI Generated Title')

    def test_should_rebuild_flag(self):
        post = self._make_post()
        # Long enough content + enabled -> rebuild.
        self.assertTrue(post._era_ai_should_rebuild({'content': self.LONG_BODY}))
        # Thin content -> skip.
        self.assertFalse(post._era_ai_should_rebuild({'content': '<p>short</p>'}))
        # Non-content write -> skip.
        self.assertFalse(post._era_ai_should_rebuild({'name': 'x'}))
        # Recursion guard -> skip.
        self.assertFalse(
            post.with_context(_era_ai_no_rebuild=True)
            ._era_ai_should_rebuild({'content': self.LONG_BODY}))

    def test_word_count_strips_html(self):
        post = self._make_post()
        self.assertEqual(post._era_ai_word_count('<p>one two three</p>'), 3)
        self.assertEqual(post._era_ai_word_count(''), 0)
        self.assertGreaterEqual(post._era_ai_word_count(self.LONG_BODY), 50)

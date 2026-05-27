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


@tagged('post_install', '-at_install')
class TestBlogAutoRebuild(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ICP = cls.env['ir.config_parameter'].sudo()
        cls.ICP.set_param('era_seo.ai_enabled', 'True')
        cls.blog = cls.env['blog.blog'].create({'name': 'Test Blog'})

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
            post.write({'content': '<h1>New Topic</h1><p>Fresh body.</p>'})
        post.invalidate_recordset()
        # Rewrite overwrites the hand-written value.
        self.assertEqual(post.seo_title, 'AI Generated Title')
        self.assertTrue(post.seo_description)
        self.assertEqual(post.seo_keywords, 'blog, seo, ai')

    def test_no_rebuild_when_ai_disabled(self):
        self.ICP.set_param('era_seo.ai_enabled', 'False')
        post = self._make_post()
        agent = _mock_agent(_FULL_REPLY)
        with patch.object(AIClient, '_resolve_agent', return_value=agent):
            post.write({'content': '<p>Edited while AI is off.</p>'})
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
            post.write({'content': '<p>Body that breaks the AI parse.</p>'})
        post.invalidate_recordset()
        self.assertEqual(post.content, '<p>Body that breaks the AI parse.</p>')
        self.assertFalse(post.seo_title)

    def test_should_rebuild_flag(self):
        post = self._make_post()
        self.assertTrue(post._era_ai_should_rebuild({'content': 'x'}))
        self.assertFalse(post._era_ai_should_rebuild({'name': 'x'}))
        self.assertFalse(
            post.with_context(_era_ai_no_rebuild=True)
            ._era_ai_should_rebuild({'content': 'x'}))

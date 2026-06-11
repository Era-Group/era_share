"""Tests for the AI article-generation prompt contract."""

from odoo.tests import TransactionCase, tagged

from odoo.addons.era_seo_suite.models.ai_client import AIClient


@tagged('post_install', '-at_install')
class TestArticleGenerationPrompt(TransactionCase):

    def test_article_prompt_uses_internal_links_and_quality_floor(self):
        prompt = AIClient._build_article_prompt(
            {
                'org_name': 'ERA',
                'summary': 'Odoo and ZATCA services in Saudi Arabia',
            },
            past_titles=['Old ZATCA Article'],
            existing_categories=['Odoo', 'Tax'],
            lang_code='en_US',
            trending_now=['zatca phase 2 integration'],
            related_pages=[{
                'title': 'ZATCA E-Invoicing Services',
                'url': '/services/zatca-e-invoicing',
            }],
            search_opportunities=[{
                'query': 'zatca phase 2 integration',
                'impressions': 320,
                'clicks': 4,
                'position': 11.8,
            }],
        )

        self.assertIn('internal_link_targets', prompt)
        self.assertIn('search_opportunities', prompt)
        self.assertIn('/services/zatca-e-invoicing', prompt)
        self.assertIn('PEOPLE-FIRST QUALITY', prompt)
        self.assertIn('AT LEAST 900 words', prompt)
        self.assertIn('FAQ section', prompt)

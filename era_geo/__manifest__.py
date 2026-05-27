{
    'name': 'ERA GEO — Generative Engine Optimization',
    'summary': 'Optimize for AI answer engines: llms.txt + AI-crawler control',
    'description': """
ERA GEO — Generative Engine Optimization
========================================
The AI-era companion to ``era_seo_manager``. Where SEO optimizes for search
engines, **GEO** optimizes how AI answer engines (ChatGPT, Perplexity,
Google AI Overviews, Claude, …) discover, crawl, and cite your content.

- **/llms.txt** (and ``/llms-full.txt``) — the emerging llmstxt.org standard:
  a clean, curated Markdown map of your most important pages and posts that
  LLMs read to understand the site. Built from your published content and the
  ERA SEO titles/descriptions.
- **AI crawler control** — decide, per bot, which AI crawlers may access the
  site (GPTBot, ChatGPT-User, OAI-SearchBot, PerplexityBot, ClaudeBot,
  anthropic-ai, Google-Extended, Applebot-Extended, CCBot, Bytespider, …).
  The choices are emitted into ``robots.txt`` automatically.
- Bilingual (Arabic + English), built on ``era_seo_manager``.

Future phases: GEO audit (citability checks), answer-ready content blocks.
    """,
    'author': 'ERA — Excellence Resources Arabia',
    'website': 'https://era.net.sa',
    'license': 'OPL-1',
    'category': 'Website/SEO',
    'version': '19.0.1.0.0',
    'depends': [
        'era_seo_manager',
    ],
    'data': [
        'security/ir.model.access.csv',
        'data/geo_ai_crawler_data.xml',
        'views/geo_ai_crawler_views.xml',
        'views/res_config_settings_views.xml',
        'views/robots_templates.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': True,
}

{
    'name': 'ERA SEO Manager — AI Auto-Fix',
    'summary': 'Claude-powered auto-fix for SEO audit findings: missing titles, descriptions, bad slugs',
    'description': """
ERA SEO — AI Auto-Fix
======================
Optional companion to ``era_seo_manager``. Wires the Anthropic Claude API
into the SEO audit dashboard so admins can fix many findings at once:

- "Suggest Fix" button on any audit finding — Claude reads the page
  content and proposes a value for the missing or malformed SEO field.
- "Apply Fix" writes the proposed value back to the target record.
- "Auto-Suggest All Critical" runs the loop across every critical finding
  in one click.
- Per-finding audit log: full prompt + response + token usage + cost.
- Prompt-caching is on by default; after the first call in a 5-minute
  window the system prompt is served from cache at ~10% of input price.

Auto-fixable checks (others surface as "no AI available" in the UI):
  - ``missing_seo_title`` -> generate one based on page content
  - ``missing_meta_description`` -> generate one
  - ``title_too_long`` -> shorten to <= 60 chars while preserving keywords
  - ``description_too_long`` -> shorten to <= 160 chars
  - ``slug_contains_uppercase`` -> mechanical (no API call)
  - ``slug_contains_stopwords`` -> AI-clean slug
  - ``slug_too_long`` -> AI-clean slug

Config: Website -> Configuration -> Settings -> ERA SEO -> AI Auto-Fix
section. API key is read from ``era_seo.ai_api_key`` (ICP) or the
``ANTHROPIC_API_KEY`` environment variable.
    """,
    'author': 'ERA — Excellence Resources Arabia',
    'website': 'https://era.net.sa',
    'license': 'OPL-1',
    'category': 'Website/SEO',
    'version': '19.0.1.0.0',
    'depends': [
        'era_seo_manager',
    ],
    'external_dependencies': {
        'python': ['anthropic'],
    },
    'data': [
        'security/ir.model.access.csv',
        'views/res_config_settings_views.xml',
        'views/seo_audit_finding_views.xml',
        'views/ai_fix_log_views.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
}

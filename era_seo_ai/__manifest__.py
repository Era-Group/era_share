{
    'name': 'ERA SEO Manager — AI Auto-Fix',
    'summary': 'Auto-fix SEO audit findings using Odoo\'s built-in AI agent',
    'description': """
ERA SEO — AI Auto-Fix
======================
Optional companion to ``era_seo_manager``. Wires Odoo's built-in **AI**
app into the SEO audit dashboard so admins can fix many findings at once:

- "Suggest Fix" button on any audit finding — the AI agent reads the page
  content and proposes a value for the missing or malformed SEO field.
- "Apply Fix" writes the proposed value back to the target record.
- "Suggest + Auto-Apply" runs the loop and auto-applies high-confidence
  proposals across every selected finding.
- Per-finding audit log: proposal, confidence, applied-by/when.

No third-party Python package and no separate API key: the LLM provider,
model, and key are whatever the admin already configured under
**Settings → AI**. This addon just calls
``ai.agent.get_direct_response()`` with SEO-specific instructions.

Auto-fixable checks (others surface as "not AI-fixable" in the UI):
  - ``missing_seo_title`` / ``title_too_long`` / ``title_too_short``
  - ``missing_meta_description`` / ``description_too_long`` / ``description_too_short``
  - ``slug_contains_uppercase`` (mechanical, no AI call)
  - ``slug_contains_stopwords`` / ``slug_too_long``
  - ``missing_og_image`` (mechanical — sets the company logo)
  - ``missing_schema`` (AI picks a JSON-LD template, attaches an instance)
  - ``image_missing_alt`` (AI writes alt text, injected into the content imgs)
  - ``thin_content`` (AI proposes an HTML block, appended on review)

Requires the Odoo **AI** app (Enterprise). Configure the agent under
**Settings → ERA SEO → AI Auto-Fix**.
    """,
    'author': 'ERA — Excellence Resources Arabia',
    'website': 'https://era.net.sa',
    'license': 'OPL-1',
    'category': 'Website/SEO',
    'version': '19.0.8.5.0',
    'depends': [
        'era_seo_manager',
        'ai',
    ],
    'data': [
        'security/ir.model.access.csv',
        'data/ai_agent_data.xml',
        'data/server_actions.xml',
        'views/res_config_settings_views.xml',
        'views/content_block_views.xml',
        'views/seo_audit_finding_views.xml',
        'views/seo_audit_run_views.xml',
        'views/ai_fix_log_views.xml',
    ],
    'post_init_hook': 'post_init_hook',
    'installable': False,
    'application': False,
    'auto_install': False,
}

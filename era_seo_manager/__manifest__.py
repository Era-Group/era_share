{
    'name': 'ERA SEO Manager',
    'summary': 'Complete SEO, schema, redirects, sitemap, and blog enhancement for Odoo 19',
    'description': """
ERA SEO Manager
===============
A unified SEO layer for Odoo 19 websites:
- Per-page meta, OG, Twitter, canonical, robots directives
- JSON-LD schema engine with 17 built-in templates
- Redirect manager (301/302) with bulk CSV import
- Sitemap and robots.txt admin UI
- SEO audit dashboard with actionable findings
- Blog enhancements: reading time, related posts, series, TOC, author profiles
- Hreflang automation for multilingual websites
- Full Arabic / RTL support
    """,
    'author': 'ERA — Excellence Resources Arabia',
    'website': 'https://era.net.sa',
    'license': 'OPL-1',
    'category': 'Website/SEO',
    'version': '19.0.3.0.0',
    'depends': [
        'base',
        'web',
        'website',
        'website_blog',
        'mail',
        'portal',
    ],
    'data': [
        'security/seo_security.xml',
        'security/ir.model.access.csv',
        'data/ir_sequence.xml',
        'data/ir_cron.xml',
        'data/seo_schema_template_data.xml',
        'data/seo_robots_default_data.xml',
        'data/seo_default_settings.xml',
        'wizards/seo_bulk_update_wizard_views.xml',
        'wizards/seo_redirect_import_wizard_views.xml',
        'wizards/seo_audit_wizard_views.xml',
        'wizards/seo_schema_preview_wizard_views.xml',
        'views/seo_status_views.xml',
        'views/res_config_settings_views.xml',
        'views/seo_schema_template_views.xml',
        'views/seo_schema_instance_views.xml',
        'views/menus.xml',
        'views/seo_redirect_views.xml',
        'views/seo_sitemap_config_views.xml',
        'views/seo_robots_rule_views.xml',
        'views/seo_audit_run_views.xml',
        'views/seo_audit_dashboard.xml',
        'views/seo_hreflang_views.xml',
        'views/blog_post_views.xml',
        'views/blog_series_views.xml',
        'views/blog_author_views.xml',
        'views/content_block_views.xml',
        'views/content_block_snippets.xml',
        'views/blog_post_templates.xml',
        'views/website_layout_templates.xml',
        'views/website_meta_templates.xml',
        'reports/seo_audit_report.xml',
    ],
    'demo': [
        'demo/demo.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'era_seo_manager/static/src/scss/backend.scss',
            'era_seo_manager/static/src/js/seo_dashboard.js',
            'era_seo_manager/static/src/xml/seo_dashboard.xml',
        ],
        'web.assets_frontend': [
            'era_seo_manager/static/src/scss/frontend.scss',
            'era_seo_manager/static/src/js/seo_snippets.js',
        ],
    },
    'post_init_hook': 'post_init_hook',
    'uninstall_hook': 'uninstall_hook',
    'installable': True,
    'application': True,
    'auto_install': False,
}

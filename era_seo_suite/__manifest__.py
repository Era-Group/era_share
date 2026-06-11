{
    'name': 'ERA SEO Suite',
    'summary': 'Unified SEO + AI + GEO + GSC + Blog suite — one module, one app',
    'description': """
ERA SEO Suite — Unified
========================
Single addon replacing the previous seven separate modules:

    era_seo_manager + era_seo_ai + era_seo_blog + era_seo_blog_ai +
    era_geo + era_geo_ai + era_gsc

Everything ships in one box: core SEO mixin + schema engine + redirects +
sitemap + robots + hreflang + audit, AI auto-fix and proactive Fill SEO,
GEO (/llms.txt + AI crawlers + GEO audit), GSC (OAuth + analytics pull),
blog enhancements (series, TOC, related, feeds), and the unified hub
(Dashboard / SEO / GEO / GSC / Settings / Guide).
    """,
    'author': 'Era Group',
    'website': 'https://era.net.sa',
    'license': 'OPL-1',
    'category': 'Website/SEO',
    'version': '19.0.3.3.3',
    'depends': [
    'base',
    'web',
    'website',
    'mail',
    'portal',
    'website_blog',
    'ai',
    # Blog Gen picks an era.ai.account for article content and one for images
    # (account.generate_text / account.generate_image) instead of per-module
    # provider/key/model settings.
    'era_ai_accounts',
    # NB: intentionally NOT depending on 'ai_crm' (it would drag in the whole
    # CRM application). ai_crm makes ai.agent.source_id required via
    # utm.source.mixin, but it is auto_install gated on ai_app + crm: an
    # SEO-only site never gets it, so the seeded agent has no source_id field
    # and installs cleanly. On sites that DO have ai_crm, the agent's source_id
    # is provisioned by the normal ai.agent create MRO (utm.source.mixin.create
    # / ai_crm._auto_init backfill) — provided no extension bypasses it (see the
    # era_voip_ext.create fix that calls super() instead of models.Model.create).
],
    'data': [
    'security/seo_security.xml',
    'security/ir.model.access.csv',
    'data/ai_agent_data.xml',
    'data/geo_ai_crawler_data.xml',
    'data/ir_cron_gsc.xml',
    'data/ir_cron_mgr.xml',
    'data/ir_cron_bulk_ai.xml',
    'data/ir_cron_weekly_audit.xml',
    'data/ir_cron_article_generator.xml',
    'data/ir_cron_article_finalize.xml',
    'data/ir_cron_run_cleanup.xml',
    'data/ir_cron_run_audit.xml',
    'data/ir_cron_sitemap.xml',
    'data/ir_sequence.xml',
    'data/schema_template_data.xml',
    'data/seo_schema_page_faq_data.xml',
    'data/seo_default_settings.xml',
    'data/seo_robots_default_data.xml',
    'data/seo_schema_template_data.xml',
    'data/seo_service_content_block.xml',
    'data/seo_suite_data.xml',
    'data/server_actions.xml',
    # Menu roots must load before any view file that uses `parent="menu_…_root"`.
    # The originals stay in their per-area files and are re-applied as no-op
    # updates when those files load later.
    'views/menu_roots.xml',

    # Wizards must load before the view files that reference their actions
    # in menuitems / buttons (e.g. seo_audit_run_views_mgr.xml uses
    # action_seo_audit_wizard).
    'wizards/seo_advice_wizard_views.xml',
    'wizards/seo_audit_wizard_views.xml',
    'wizards/seo_bulk_update_wizard_views.xml',
    'wizards/seo_redirect_import_wizard_views.xml',
    'wizards/seo_schema_preview_wizard_views.xml',
    'wizards/seo_suite_onboarding_wizard_views.xml',

    # === Base layer (era_seo_manager origin) ===
    # Action-defining files first so later files (settings, hub, menus)
    # can reference them via `name="%(action_X)d"` / `action="action_X"`.
    'views/content_block_views_mgr.xml',
    'views/seo_audit_dashboard.xml',
    'views/seo_audit_run_views_mgr.xml',
    'views/seo_hreflang_views.xml',
    'views/seo_redirect_views.xml',
    'views/seo_robots_rule_views.xml',
    'views/seo_schema_instance_views.xml',
    'views/seo_schema_template_views.xml',
    'views/seo_sitemap_config_views.xml',
    'views/seo_status_views.xml',
    'views/robots_templates.xml',
    'views/website_layout_templates.xml',
    'views/website_meta_templates.xml',
    # Settings view uses several of the actions defined above.
    'views/res_config_settings_views_mgr.xml',

    # === Extension layers (each extends the base) ===
    # Action-defining files first within this layer too.
    'views/content_block_views_ai.xml',
    'views/content_block_views_geo.xml',
    'views/content_block_snippets.xml',
    'views/seo_audit_finding_views.xml',
    'views/seo_audit_run_views_ai.xml',
    'views/ai_fix_log_views.xml',
    'views/geo_ai_crawler_views.xml',
    'views/gsc_account_views.xml',
    'views/gsc_keyword_views.xml',
    'views/gsc_query_views.xml',
    'views/gsc_site_views.xml',
    # Settings views consume actions defined above.
    'views/res_config_settings_views_ai.xml',
    'views/res_config_settings_views_geo.xml',
    'views/res_config_settings_views_gsc.xml',

    # Blog layer (extends manager) and blog+ai layer (extends both).
    'views/blog_author_views.xml',
    'views/blog_category_views.xml',
    'views/blog_landing_templates.xml',
    'views/blog_post_templates.xml',
    'views/blog_post_views_blog.xml',
    'views/blog_series_views.xml',
    'views/blog_post_views_blogai.xml',

    # Hub view (presents everything).
    'views/seo_suite_hub_views.xml',

    # === Menus last (need actions defined above) ===
    'views/menus.xml',
    'views/menus_mgr.xml',
    'views/menus_blog.xml',
    'views/menus_gsc.xml',
    # consolidate.xml must load AFTER the menus and settings-view records
    # it disables (defined above). Loading it earlier triggers Odoo to
    # CREATE the records instead of updating them, which crashes with
    # `null value in column "name"` on ir.ui.menu's NOT NULL constraint.
    'data/consolidate.xml',
    'reports/seo_audit_report.xml',
],
    'demo': [
    'demo/demo.xml',
],
    'assets': {
    'web.assets_frontend': [
        'era_seo_suite/static/src/scss/frontend.scss',
        'era_seo_suite/static/src/js/seo_snippets.js',
    ],
    'web.assets_backend': [
        'era_seo_suite/static/src/scss/backend.scss',
        'era_seo_suite/static/src/js/seo_dashboard.js',
        'era_seo_suite/static/src/js/blog_gen_poll.js',
        'era_seo_suite/static/src/xml/seo_dashboard.xml',
    ],
},
    'installable': True,
    'application': True,
    'auto_install': False,
    'post_init_hook': 'post_init_hook',
}

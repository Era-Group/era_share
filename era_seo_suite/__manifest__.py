{
    'name': 'ERA SEO Suite — Unified Hub',
    'summary': 'One-screen hub: dashboard, SEO, GEO, GSC, settings, and guides',
    'description': """
ERA SEO Suite — Unified Hub
============================
A single, prominent entry point in the main menu (**ERA SEO Suite**) that
opens one screen with tabs for the whole SEO suite:

- **Dashboard** — at-a-glance KPIs (pages, last audit, open findings,
  GSC clicks, llms.txt status, …).
- **SEO** — launchpad: pages, schema templates, redirects, sitemap, robots,
  hreflang, audits, content blocks.
- **GEO** — llms.txt status, AI crawlers, GEO audit.
- **GSC** — accounts, sites, queries.
- **Settings** — the most-used toggles inline + buttons to the full
  configuration panel of every module.
- **Guide** — collapsed, on-page setup walkthroughs for AI, GEO, GSC.

The existing per-feature menus under Website → SEO continue to work; the
new hub is an additional, faster entry point.
    """,
    'author': 'ERA — Excellence Resources Arabia',
    'website': 'https://era.net.sa',
    'license': 'OPL-1',
    'category': 'Website/SEO',
    'version': '19.0.1.2.0',
    'depends': [
        'era_seo_manager',
        'era_geo',
        'era_gsc',
    ],
    'data': [
        'security/ir.model.access.csv',
        'data/seo_suite_data.xml',
        'views/seo_suite_hub_views.xml',
        'views/menus.xml',
    ],
    'installable': True,
    'application': True,
    'auto_install': True,
}

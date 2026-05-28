{
    'name': 'ERA SEO Suite — Unified Hub',
    'summary': 'One-screen hub: dashboard, SEO, GEO, GSC, settings, and guides',
    'description': """
ERA SEO Suite — Unified Hub
============================
The single, exclusive user-facing entry point for the whole SEO suite —
one main-menu app, one screen, six tabs:

- **Dashboard** — at-a-glance KPIs (pages, last audit, open findings,
  GSC clicks, llms.txt status, …).
- **SEO / GEO / GSC** — launchpad to every feature in the suite.
- **Settings** — every ir.config_parameter the suite owns (21 fields, with
  per-field help) lives here; the legacy per-module Settings blocks under
  Website → Configuration → Settings are turned off.
- **Guide** — collapsed, on-page setup walkthroughs for AI, GEO, GSC.

The underlying modules (era_seo_manager, era_seo_ai, era_geo, era_gsc, the
blog/ai bridges) stay intact for tests and migrations; this addon turns
off their scattered menus + Settings blocks so the user experience is
fully unified through the hub.
    """,
    'author': 'ERA — Excellence Resources Arabia',
    'website': 'https://era.net.sa',
    'license': 'OPL-1',
    'category': 'Website/SEO',
    'version': '19.0.2.0.0',
    'depends': [
        'era_seo_manager',
        'era_seo_ai',
        'era_geo',
        'era_gsc',
    ],
    'data': [
        'security/ir.model.access.csv',
        'data/seo_suite_data.xml',
        'views/seo_suite_hub_views.xml',
        'views/menus.xml',
        # noupdate=1 — turns off the legacy SEO menus + per-module Settings
        # blocks once the suite is installed. MUST load last so the menu +
        # view xmlid refs it disables already exist.
        'data/consolidate.xml',
    ],
    'installable': True,
    'application': True,
    'auto_install': True,
}

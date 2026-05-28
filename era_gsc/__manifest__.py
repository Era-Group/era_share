{
    'name': 'ERA SEO — Google Search Console',
    'summary': 'Connect Google Search Console: pull search performance + submit sitemaps',
    'description': """
ERA SEO — Google Search Console
================================
Connect one or more Google accounts via OAuth 2.0 and pull search-performance
data into Odoo for every verified GSC property.

- ``era.gsc.account`` — connected Google account with refresh-token-based
  auth (Connect button starts the standard OAuth flow).
- ``era.gsc.site`` — verified GSC properties under each account (the connector
  discovers them after the first authorization).
- ``era.gsc.query`` — daily search-analytics rows (date, query, clicks,
  impressions, CTR, position) pulled per site.
- Daily cron + a manual **Pull Now** button on each site.
- Backend views under **Website → SEO → GSC**.

Bring-your-own credentials: the admin creates an OAuth 2.0 Client (Web) in
Google Cloud Console, enables the Search Console API, and pastes the
client id/secret in ERA GSC settings. Tokens are stored on the account
record; only refresh tokens persist across restarts.
    """,
    'author': 'ERA — Excellence Resources Arabia',
    'website': 'https://era.net.sa',
    'license': 'OPL-1',
    'category': 'Website/SEO',
    'version': '19.0.1.1.0',
    'depends': [
        'era_seo_manager',
    ],
    'data': [
        'security/ir.model.access.csv',
        'data/ir_cron.xml',
        'views/gsc_account_views.xml',
        'views/gsc_site_views.xml',
        'views/gsc_query_views.xml',
        'views/res_config_settings_views.xml',
        'views/menus.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': True,
}

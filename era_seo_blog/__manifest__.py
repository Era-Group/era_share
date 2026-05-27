{
    'name': 'ERA SEO Manager — Blog Enhancements',
    'summary': 'Reading time, TOC, related posts, series, authors, FAQ, RSS/Atom/JSON feeds for website_blog',
    'description': """
ERA SEO — Blog Enhancements
============================
Optional companion to ``era_seo_manager``. Install on sites that use the
stock ``website_blog`` module and want the full editorial / SEO treatment:

- Reading time + word count auto-computed on every post
- Auto-generated table of contents from h2/h3 headings
- Smart excerpt fallback (custom → subtitle → first 200 chars)
- Series with prev/next navigation
- Taxonomic categories (distinct from folksonomic tags)
- Standalone author profiles (with optional `res.users` link)
- Per-post FAQ entries (renders accordion + FAQPage JSON-LD)
- Related posts (auto, top 4 by series + tag overlap)
- RSS 2.0, Atom 1.0, JSON Feed 1.1 with per-tag/category/author/series variants
- Syndication-friendly canonical override per post
    """,
    'author': 'ERA — Excellence Resources Arabia',
    'website': 'https://era.net.sa',
    'license': 'OPL-1',
    'category': 'Website/SEO',
    'version': '19.0.2.0.0',
    'depends': [
        'era_seo_manager',
        'website_blog',
    ],
    'data': [
        'security/ir.model.access.csv',
        'data/schema_template_data.xml',
        'views/menus.xml',
        'views/blog_series_views.xml',
        'views/blog_category_views.xml',
        'views/blog_author_views.xml',
        'views/blog_post_views.xml',
        'views/blog_post_templates.xml',
        'views/blog_landing_templates.xml',
    ],
    'post_init_hook': 'post_init_hook',
    'installable': True,
    'application': False,
    'auto_install': True,
}

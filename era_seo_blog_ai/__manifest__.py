{
    'name': 'ERA SEO Blog — AI',
    'summary': 'AI SEO fill buttons on blog posts + auto-rebuild on content change',
    'description': """
ERA SEO Blog — AI bridge
========================
Glue between ``era_seo_blog`` and ``era_seo_ai``. **Auto-installs** only when
both are present, so neither module takes a hard dependency on the other
(not every site has a blog; not every site has the AI app).

- **AI buttons on the blog post SEO tab** — *AI: Fill SEO* (fills only the
  empty meta fields) and *AI: Rewrite SEO* (regenerates them all). Same
  one-click flow website pages already have, now discoverable on the post
  form instead of only in the Action menu.
- **Auto-rebuild on content change** — when a blog post's ``content`` is
  edited, all SEO meta fields are regenerated from the new content (every
  installed website language), so the meta, Open Graph, keywords, and the
  JSON-LD that reads those fields stay in sync with the body. Runs as a
  system automation (no SEO-Manager group needed for the editor) and is
  best-effort: a failed or slow AI call never blocks the save.

Both honour the **AI Auto-Fix enabled** flag — when AI is disabled the
auto-rebuild is skipped and the buttons surface the usual "disabled" notice.
    """,
    'author': 'ERA — Excellence Resources Arabia',
    'website': 'https://era.net.sa',
    'license': 'OPL-1',
    'category': 'Website/SEO',
    'version': '19.0.1.2.0',
    'depends': [
        'era_seo_blog',
        'era_seo_ai',
    ],
    'data': [
        'views/blog_post_views.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': True,
}

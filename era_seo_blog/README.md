# `era_seo_blog`

Blog enhancement layer for Odoo 19 `website_blog`, built on top of
`era_seo_manager`. Optional companion addon — installs only on sites that
actually have a blog.

## What it adds

| Model | Purpose |
|---|---|
| `era.blog.series` | Sequence of posts forming one narrative arc, with prev/next nav and a landing page at `/blog/series/<slug>`. |
| `era.blog.category` | Taxonomic (one-per-post) hierarchical category. Distinct from folksonomic tags. |
| `era.blog.author` | Standalone author profile, optional `res.users` link, avatar + bio + socials. |
| `era.blog.faq` | Per-post Q&A pair — renders both as a visible accordion and as `FAQPage` JSON-LD. |
| `blog.post` (extended) | 17 new fields: subtitle, reading time, word count, excerpt, TOC, series, category, author, FAQ, related posts, display toggles, syndication canonical. |

## Feeds

| URL | Format |
|---|---|
| `/blog/feed/rss` | RSS 2.0 |
| `/blog/feed/atom` | Atom 1.0 |
| `/blog/feed/json` | JSON Feed 1.1 |
| `/blog/tag/<slug>/feed/<format>` | per-tag |
| `/blog/category/<slug>/feed/<format>` | per-category |
| `/blog/author/<slug>/feed/<format>` | per-author |
| `/blog/series/<slug>/feed/<format>` | per-series |

All feeds are public, cached for 10 minutes via `Cache-Control`, limited
to 50 most recent posts.

## Install

```bash
odoo-bin -c odoo.conf -d <db> -i era_seo_blog --stop-after-init
```

Auto-installs on any DB that has both `era_seo_manager` and `website_blog`.

## Reading time

Computed from `content` word count divided by 200 (standard reading speed).
Minimum 1 minute for non-empty posts; 0 when content is empty.

## Related posts algorithm

1. Up to 2 from the same series (excluding self).
2. Fill remaining slots by descending shared-tag count.
3. Tie-break by `published_date` desc, then `id` desc.
4. Unpublished posts always excluded.

## License

OPL-1.

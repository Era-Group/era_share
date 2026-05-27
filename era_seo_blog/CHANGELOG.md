# Changelog

All notable changes to `era_seo_blog` are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [19.0.1.0.0] — 2026-05-27

### Added — Initial release (extracted from `era_seo_manager`)

This addon was carved out of `era_seo_manager` to keep the base SEO addon
useful on sites that don't run `website_blog`. Auto-installs whenever both
`era_seo_manager` and `website_blog` are present.

- `blog.post` extension (17 new fields):
  subtitle, reading time, word count, excerpt + auto-fallback, series +
  position, category, related posts (top 4 by series + tag overlap),
  auto-generated TOC, four display toggles, FAQ entries, author profile
  link, syndication canonical override.
- `era.blog.series` model with unique slug, hierarchy via `_parent_store`-
  style ordering, cover image, post listing.
- `era.blog.category` model — taxonomic (one-per-post), hierarchical via
  `parent_id`, cycle detection, unique slug.
- `era.blog.author` model — standalone profiles with optional `res.users`
  link, social URLs, avatar.
- `era.blog.faq` — per-post Q&A pairs, ondelete cascade with parent post.
- Feed controllers (RSS 2.0, Atom 1.0, JSON Feed 1.1) at `/blog/feed/{rss,atom,json}`
  plus per-tag/category/author/series variants. 10-minute Cache-Control,
  50-item limit per response.
- Backend admin: lists + forms for all four new models, "Blog" submenu
  under **Website → Configuration → SEO**, new tabs ("ERA Blog", "Reading
  Stats") on the `blog.post` form.
- Tests: reading time, word-count HTML strip, excerpt fallback, TOC
  generation, series neighbors, canonical override, slug validation,
  category cycle detection, author user-link.

### Deferred

- Frontend template overrides on `website_blog.blog_post_complete`
  (subtitle, TOC sidebar, author box, FAQ accordion, share buttons,
  related-posts block, series navigation, breadcrumbs).
- Auto-attach BlogPosting / BreadcrumbList / FAQPage schema instances on
  blog post render.

Both ship together in `19.0.1.1.0`.

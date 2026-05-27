# Changelog

All notable changes to `era_seo_blog` are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [19.0.2.2.0] — 2026-05-27

### Changed — compact post header (two columns)

The top of the blog post form stacked "blog details" (Blog, Title,
Subtitle, Tags) and "Publishing Options" (Author, dates, views) in one tall
single column. They now sit side by side in a two-column wrapper group, so
the header takes about half the vertical space.

## [19.0.2.1.0] — 2026-05-27

### Fixed — Word Count stuck at 3; empty stock SEO tab

- **Word Count / Reading Time** read only `content` in one language. The
  English *source* content often stays the default `Start writing here...`
  stub (3 words) while the real article is a translation, so the stored
  stat reported 3. The compute now counts the **richest language variant**
  of `content` (via stored translations), and a post-migration recomputes
  existing posts.
- **Empty "SEO" tab.** 19.0.2.0.0 hid the stock Meta fields but left an
  empty stock SEO tab. The whole stock SEO page is now hidden (its fields
  are dev-only and synced from the ERA SEO tab), so there's no empty tab —
  just **ERA SEO**, **ERA Blog**, **Reading Stats**.

## [19.0.2.0.0] — 2026-05-27

### Fixed — duplicate Title/Description on the post form; ERA SEO now visible

The SEO tab showed two sets of Title/Description/Keywords — Odoo's stock
`website_meta_*` and ERA's `seo_*` — that weren't linked, and the ERA
fields were buried *inside* the stock SEO page, which is dev-mode only
(`base.group_no_one`), so normal SEO managers couldn't see them at all.

- **Sync.** `blog.post` now mirrors `seo_* ↔ website_meta_*` bidirectionally
  (last-write-wins, guarded by `_era_no_sync`), the same pattern
  `website.page` already uses. So the ERA fields (and the AI fill) drive
  Odoo's native meta/sitemap, and the stock "Optimize SEO" dialog flows
  back into the ERA fields. A post-migration backfills existing posts.
- **Layout.** The ERA SEO fields moved out of the dev-only stock SEO page
  into their own visible **ERA SEO** tab (Title & Description, Open Graph,
  Twitter, Indexing & Canonical, Sitemap). The stock Meta
  Title/Description/Keywords group is hidden on the form (kept in sync
  underneath), so editors work in one place.

## [19.0.1.1.1] — 2026-05-27

### Fixed

- Frontend overrides on `website_blog.blog_post_complete` targeted
  `<section id="o_wblog_post_content">` but Odoo 19 wraps content in
  `<div>`. Switched the xpath anchors to
  `//div[hasclass('o_wblog_post_content_field')]` for before-body and
  `//div[hasclass('o_wblog_post_footer')]` for after-body so the six
  affected overrides (TOC, author box, FAQ accordion, related, series
  nav, share buttons) actually render.
- 1.1.0 → 1.1.1 migration script re-runs the schema backfill so staging
  sites that already had 1.1.0 installed (and therefore missed the 1.1.0
  migration, since the version delta only fires once per upgrade) still
  get BlogPosting / BreadcrumbList / FAQPage instances attached on
  `-u era_seo_blog`.

## [19.0.1.1.0] — 2026-05-27

### Added — Frontend rendering + auto-schema attach

- **Auto-attached JSON-LD schemas** on `blog.post`:
    - `BlogPosting` and `BreadcrumbList` attach on every post create.
    - `FAQPage` attaches when at least one `era.blog.faq` row exists; the
      per-instance `data_json` is rebuilt on every FAQ create/write/unlink
      so the `mainEntity` array stays in sync with the visible accordion.
    - A new schema template `blog_faq_page` (separate from the stock
      `faq_page` template) renders the Q&A list via a `{{ faq_main_entity | json }}`
      placeholder.
    - `post_init_hook` backfills existing posts on first install.

- **Frontend template overrides** on `website_blog.blog_post_complete`:
    - Subtitle below the H1
    - Reading time + word count meta line
    - Collapsible TOC (gated by `era_show_toc`)
    - Author box with avatar, role, bio (gated by `era_show_author_box`)
    - FAQ accordion (Bootstrap)
    - Related-posts card grid (gated by `era_show_related`)
    - Series prev/next nav with link to the series landing
    - Share buttons for X, LinkedIn, Facebook, WhatsApp (gated by `era_show_share_buttons`)

- **Landing-page controllers + template** for the URLs the new auto-attached
  schemas (and the new post templates) link to:
    - `/blog/series/<slug>` → series landing
    - `/blog/category/<slug>` → category landing
    - `/blog/author/<slug>` → author landing
  All three render the shared `era_seo_blog.blog_landing` QWeb template;
  the controller sets `main_object` so the corresponding ERA SEO schema
  instances attached to the landing record also render in `<head>`.

### Tests

- `tests/test_auto_schemas.py`: BlogPosting + BreadcrumbList attach,
  FAQPage lifecycle (attach / detach / data_json content), idempotent
  re-sync.
- `tests/test_feeds.py`: RSS / Atom / JSON status codes, content types,
  Cache-Control header, unknown-format 404.

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

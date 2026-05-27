# Changelog

All notable changes to `era_seo_manager` are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [19.0.8.1.0] — 2026-05-28

### Added — ready "SEO service value" content block

Ships a marketing `era.content.block` (`code = seo_service_value`, Arabic
copy) that presents the value of the SEO service — wider search visibility
via auto structured data, bilingual content, always-fresh data, central
reuse, no-code editing, ongoing auditing. `noupdate="1"` so edits in the
website editor survive upgrades. Drop it on any page from **Content Blocks**
or `t-call` it by code.

## [19.0.8.0.0] — 2026-05-27

### Added — Phase 8: content blocks + website-builder snippets (SPEC §14)

- **`era.content.block`** is now a full model (was a stub): `name`, unique
  `code`, `block_type`, translatable `content_html`, `schema_template_id`,
  and it inherits `era.seo.mixin`. Backend admin under **Website → SEO →
  Content Blocks**.
- **Seven draggable snippets** in a new **ERA SEO** category of the builder
  blocks panel: FAQ, Breadcrumbs, Call to Action, Author Box, Related Posts,
  Feature Grid, Pricing Table.
- **Auto structured data**, injected on the published page only (so it's
  never saved into the page arch and always matches the live content):
  - **FAQ** snippet → `FAQPage` JSON-LD built from the accordion Q&A.
  - **Breadcrumbs** snippet → `BreadcrumbList` JSON-LD built from the URL,
    which also renders the visible breadcrumb chain.
  - Implemented as Odoo 19 public `Interaction`s in
    `static/src/js/seo_snippets.js`.
- Tests: model (unique/charset code, mixin fields, seo path) and a render
  check that all seven snippet templates produce their root markup, plus the
  FAQ/Breadcrumbs JS hooks. One demo content block.

### Notes

- The JSON-LD injection and the in-builder drag/drop are browser behaviors;
  validate them interactively on staging (unit tests cover the model and the
  server-side template render, not the runtime JS).

## [19.0.7.0.0] — 2026-05-27

### Changed — audit content checks are now per-language

The title/description checks used to read `seo_title` / `seo_description`
in a single language (the audit user's), so a too-short or missing
description in another language slipped through — e.g. an Arabic title with
an English description edited down to "Discover ERA's" was never flagged.

- The six text-content checks (missing/long/short/duplicate title and
  description) now loop **every installed website language** and read each
  field with `with_context(lang=...)`. Each finding records the offending
  `lang_id`, and its name carries the language code (`Meta Description Too
  Short (14 chars) [ar_001]`).
- The finding upsert key and unique index are now
  `(check_code, res_model, res_id, lang_id)` — one open finding per
  defect *per language*. Language-agnostic checks (slug, H1, schema,
  redirects) keep `lang_id` empty.
- Auto-resolve and the dedup logic are language-aware.
- New `lang_id` column/filter/group-by on the Audit Findings views.
- Pre-migration `19.0.7.0.0` drops the old `(check_code, res_model,
  res_id)` unique index so the language-aware one can replace it.

Paired with `era_seo_ai` ≥ 19.0.5.0.0: when a finding is language-scoped,
"Suggest Fix" rewrites **only that language**, so fixing a short English
description never clobbers a good Arabic one.

## [19.0.6.2.0] — 2026-05-27

### Fixed — "Optimize SEO" dialog edits no longer lost

The website builder's **Search Engine Optimization** dialog edits the stock
`website_meta_*` fields, but the frontend renders the ERA `seo_*` fields and
our sync was **one-way (ERA → stock)**. So a description typed into that
dialog was never shown on the site and got reverted to the ERA value on the
next sync — it looked like "nothing saved" (and produced the Arabic-title /
English-description mismatch).

- `website.page` sync is now **bidirectional, last-write-wins**: writing a
  stock `website_meta_*` field mirrors it back into the matching ERA `seo_*`
  field (`_sync_stock_to_era`), and writing an ERA field still mirrors to
  stock (`_sync_era_to_stock`). The reverse sync runs in the record's
  current language, so a dialog edit in Arabic updates the Arabic ERA
  translation.
- Both internal syncs use the `_era_no_sync` context guard, so there's no
  write recursion. When both sides are written at once, ERA wins.
- `create()` seeds both directions, so a page made through the dialog gets
  its ERA fields populated immediately.
- Tests: dialog edit → ERA propagation, edit survives an unrelated re-save
  (no revert), and ERA-wins-when-both-written.

## [19.0.6.1.0] — 2026-05-27

### Fixed — audit findings no longer duplicate across runs

Every audit run used to create a fresh finding row, so the same defect on
the same page piled up one row per run (5 runs → 5 identical "Meta
Description Too Short" rows). Findings are now **upserted** by
`(check_code, res_model, res_id)`:

- `_add_finding` searches for an existing finding for the same check +
  target and **updates it in place** (refreshing severity / name /
  details and re-pointing `run_id` at the current run); only genuinely
  new defects create a row. AI-fill fields on the existing finding are
  preserved.
- A re-detected finding that had been resolved is **reopened**.
- After each run, findings on the scanned pages that were *not* re-detected
  **auto-resolve** (the issue was fixed) — scoped to the pages actually
  scanned, so other websites / unscanned records are untouched.
- `era.seo.audit.finding` gains a UNIQUE index on
  `(check_code, res_model, res_id)`; `run_id` is now "Last Detected In"
  (`ondelete='set null'`, optional) so findings persist when an old run is
  deleted.
- Pre-migration `19.0.6.1.0` de-dups existing finding rows (keeps the
  newest per key, carries "resolved" forward) before the index is created.
- Tests: re-run-no-duplicate, update-in-place (same row id, run_id
  re-points), fixed-issue-auto-resolves, reappearing-issue-reopens.

## [19.0.6.0.0] — 2026-05-27

### Added — Phase 7 (SEO Audit Dashboard, SPEC §13)

- `era.seo.audit.run` model with state machine (draft / running / done /
  failed), start + finish timestamps, computed counts (critical /
  warning / info / total / unresolved), optional per-website scope, and
  `error_message` for failed runs.
- `era.seo.audit.finding` model: one row per (run, check, target). Each
  finding carries severity, check code + name, polymorphic
  (res_model, res_id), URL, details, suggested fix, and a resolved
  flag with timestamp + user.
- **22 audit checks** implemented as `_check_*` methods on `audit.run`:
  missing/long/short/duplicate seo_title; missing/long/short/duplicate
  meta description; missing og_image; missing canonical; noindex in
  sitemap; missing/multiple H1; missing alt; slug too long /
  uppercase / stopwords; missing schema; thin content; orphan page;
  broken redirect chain; redirect loop. Each check is wrapped in its
  own savepoint so one failing check doesn't abort the run.
- Wizard at **SEO → SEO Audit → Run SEO Audit** to launch a run from
  the UI; admin can scope to one website.
- Backend admin: list / form / search for both `audit.run` and
  `audit.finding`, decorated by severity, with badges and one-click
  "Open Target" / "Mark Resolved" actions. Three new menu entries
  under SEO → SEO Audit (sequence 60).
- Nightly cron `cron_seo_audit_nightly` (disabled by default; admin
  flips `active` to enable).
- Tests covering state transitions, missing-title, long-title,
  duplicate-title, noindex-in-sitemap, slug uppercase, resolved flow,
  open-target action, and counts compute.

## [19.0.5.1.0] — 2026-05-27

### Fixed — Hreflang stays in sync with website-level language changes

- **Default-lang flip used to leave hreflang rows pointing at the old
  URL prefix** (e.g. Arabic stayed at `/ar/about` after being promoted
  to default — Odoo's routing moved it to `/about` but the hreflang
  row didn't follow). Added a `website.write()` override that detects
  changes to `default_lang_id` or `language_ids` and triggers
  `era.seo.hreflang._era_resync_all_records()`, which iterates every
  concrete model carrying `era.seo.mixin` (detected via duck-typing
  on `_sync_era_hreflang_entries`) and recomputes every row.
- Switched the rendered `hreflang` attribute from the locale code
  (`ar_001`) to the language's ISO code (`ar`) when present, falling
  back to a hyphen-normalised locale (`en-US`) otherwise. Google's
  recommendation is ISO 639-1 / BCP 47.
- New admin action **Resync All Hreflang Rows** under the Hreflang
  list's Action menu, plus a programmatic
  `era.seo.hreflang.action_resync_all()` server-action entry point.
- 19.0.5.1.0 post-migrate runs the resweep automatically so existing
  staging databases get fixed without re-saving each page.

## [19.0.5.0.0] — 2026-05-27

### Added — Phase 6 (Hreflang automation, SPEC §12)

- `era.seo.hreflang` model with polymorphic `(res_model, res_id, lang_id)`
  uniqueness, `is_xdefault` flag (one per record group, enforced),
  `is_manual` flag so admin overrides survive auto-sync.
- `era.seo.mixin._sync_era_hreflang_entries()` upserts one row per active
  website language, marks the default-lang row as `x-default`, prunes
  stale rows when a language leaves the website, and respects `is_manual`.
- `website.page` create/write/unlink hooks: auto-sync on create, refresh
  on URL/website_id changes, cascade-delete on unlink.
- `era_seo_blog/blog.post` gets the same wiring: hreflang sync on
  create/write/unlink, plus a polymorphic FK cleanup for both
  `era.seo.schema.instance` and `era.seo.hreflang` rows.
- Frontend: new QWeb template `era_seo_manager.hreflang_links` and a
  priority-30 inherit on `website.layout` that emits one
  `<link rel="alternate" hreflang="…" href="…"/>` per row plus the
  `hreflang="x-default"` row. Gated by the
  `era_seo.hreflang_enabled` ICP kill switch.
- Admin UI: list / form / search on `era.seo.hreflang`, plus a
  **Website → Configuration → SEO → Hreflang** menu entry (sequence 30).
- Migration `19.0.5.0.0/post-migrate.py` backfills hreflang on every
  existing website.page so the admin UI is immediately populated.
- Tests: x-default uniqueness constraint, lang-prefix path computation,
  active-only retrieval, manual override preservation, unlink cascade.

## [19.0.4.0.0] — 2026-05-27

### Changed — `website_blog` dependency removed

`era_seo_manager` no longer depends on `website_blog`. Sites that don't
run a blog can now install the core SEO layer without pulling in the
blog module.

Blog enhancements (reading time, TOC, related posts, series, categories,
authors, FAQ, RSS/Atom/JSON feeds) moved to the new sibling addon
**`era_seo_blog`**, which depends on both `era_seo_manager` and
`website_blog` and auto-installs whenever both are present.

### Removed

- `models/blog_post.py`, `blog_series.py`, `blog_category.py`,
  `blog_author.py` (stubs that anticipated Phase 5 — now live in
  `era_seo_blog`)
- `controllers/blog.py`, `controllers/feed.py` (stubs)
- `views/blog_post_views.xml`, `blog_series_views.xml`,
  `blog_author_views.xml`, `blog_post_templates.xml`
- `tests/test_blog_extensions.py`
- Blog ACL rows in `security/ir.model.access.csv`

### Migration

Existing sites that have `website_blog` will pick up `era_seo_blog`
automatically on next module-list update. No data loss — the blog SEO
fields on existing posts were never written to by Phase 1-3 since the
stub models had no extra fields.

## [19.0.3.1.0] — 2026-05-27

### Added — Phase 3 polish

- **Query string forwarding** (`models/ir_http.py`): tracking parameters
  (UTM, affiliate, etc.) on the inbound URL are now appended to the
  redirect target instead of silently dropped. When the target already
  carries a query string, the inbound params are merged after a `&`.
- **Lang prefix handling**: a rule for `/old` now also matches
  `/ar/old`, `/en/old`, etc. The hook strips the active website's
  language URL prefix before lookup so admins author one rule for all
  languages instead of N rules per language.
- **Trailing-slash equivalence**: `/foo` and `/foo/` resolve to the
  same rule. If the first lookup misses, the hook retries with the
  trailing slash toggled.
- **System-path skip-list**: `/web/`, `/my/`, `/odoo/`, `/static/`,
  `/website/static/`, `/longpolling/`, `/web_editor/`, `/_health` are
  never intercepted. Prevents an admin from accidentally redirecting
  `/web/login` and locking the instance.
- **Method guard**: only `GET` and `HEAD` requests trigger redirect
  resolution. `POST/PUT/PATCH/DELETE` on missing paths are usually API
  calls and silently redirecting them breaks clients.
- **Coexistence docs**: module docstring now documents the precedence
  with stock `website.rewrite` (stock fires first; ERA on miss).

### Tests

- 16 new tests covering the polish surface
  (`tests/test_redirects.py::TestRedirectPolish`,
  `TestQueryStringForwarding`).

## [19.0.3.0.0] — 2026-05-27

### Added — Phase 3 (Redirect Manager)

- `era.seo.redirect` model: 301/302/307/308/410 redirects with optional
  website + language scope, regex patterns with backreference substitution,
  hit counter, last-hit timestamp, and origin tracking
  (manual / import / auto_404 / auto_rename). SPEC §9.1.
- `era.seo.redirect.log` model: 404 capture with hit-count upsert keyed on
  `(path, website_id)`, self-referer stripping (privacy + log spam), and a
  daily vacuum cron that prunes resolved entries after 30 days and
  unresolved entries after 90 days. SPEC §9.4.
- `ir.http._serve_fallback` override (`models/ir_http.py`): redirect lookup
  runs before the standard 404 response. Loop protection via a short-lived
  cookie (`era_seo_hops`) — once a chain exceeds `REDIRECT_HOP_LIMIT=5` we
  return a 508. SPEC §9.2.
- `era.seo.redirect.import.wizard`: CSV bulk import (UTF-8 + cp1256
  fallback). Required columns `source` and `target`; optional `type`,
  `website`, `lang`, `notes`, `is_regex`. Dry-run mode emits a counts and
  errors report without writing; actual run upserts by
  `(source, website, lang)` so re-imports update instead of duplicating.
  SPEC §9.3.
- Admin UI: **Website → Configuration → SEO** now exposes "Redirects",
  "Import Redirects", and "404 Log" entries. List views show hit counters
  inline; the 404 log has a one-click "Create Redirect" action that
  pre-fills the source path and marks the new rule `created_from='auto_404'`.
- Daily `ir.cron` (`cron_vacuum_redirect_log`) calls
  `era.seo.redirect.log._vacuum_old_entries()`.
- ACLs for both new models + the wizard. `era.seo.redirect` already had
  manager/user rows from earlier phases; added log + wizard entries.
- Tests (`tests/test_redirects.py`): plain match, scope priority,
  regex with backreferences, `re.fullmatch` semantics, 410 Gone, path
  normalization (query/fragment/absolute URL handling), self-loop
  rejection, invalid-regex rejection, 404 log upsert + self-referer
  drop, CSV import dry-run / real / idempotent re-import / missing
  column / invalid type.

### Removed

- `models/ir_ui_view.py` placeholder — never held actual code; module
  imports cleaned up.

## [19.0.2.2.0] — 2026-05-27

### Fixed — Absolute URLs in JSON-LD

- **`"url": "/"` on every page** (`models/seo_schema_engine.py`, `data/seo_schema_template_data.xml`):
  Templates resolved `{{ website.domain | default("") }}` to `""` when the
  per-website Domain field was blank, producing `"@id": "/#organization"` and
  `"url": "/"` — both flagged by Google Rich Results Test. Replaced with a new
  `{{ site_url }}` context value that falls back to `web.base.url` and upgrades
  bare hosts (`example.com`) to `https://example.com`. All 17 built-in
  templates migrated; trailing slash is normalized away so paths concatenate
  cleanly. Run `-u era_seo_manager` on existing sites to refresh the bodies.

### Fixed — Duplicate `<meta name="robots">`

- **Two robots tags on every ERA page** (`views/website_meta_templates.xml`):
  Step 5 of `website_layout_templates.xml` already replaces the stock
  `<meta name="robots">` with the ERA directive; `meta_tags` was emitting a
  second one. Removed the duplicate from `meta_tags`; the layout xpath is the
  single source of truth. Regression test added (`test_single_robots_meta_emitted`).

### Changed — Schema preload moved out of QWeb

- **Two ad-hoc `search()` calls in `era_seo_schema_ld` template** replaced
  with a single `era.seo.schema.instance._get_for_render(main_object, website)`
  model method. Combines site-wide and page-specific instances in one SQL
  query and returns them ordered (site-bound first, then page-bound, sequence
  ascending within each group). Satisfies CLAUDE.md §9 "no queries inside
  QWeb templates". Tests cover ordering, inactive filtering, and the
  homepage-with-no-main_object case.

## [19.0.2.1.0] — 2026-05-27

### Added — Phase 2 post-release fixes & UX

- `era.seo.status` TransientModel: live overview dashboard (Website → SEO → Overview)
  showing org identity, site verification, page coverage stats, schema engine totals,
  redirect and hreflang counts, and module version — all recomputed on open
- `views/seo_status_views.xml` + `action_seo_status` window action; menu item wired as
  first child of the SEO root menu (sequence 5)
- ERA SEO settings page (`res_config_settings_views.xml`): complete instructional
  rewrite — intro banner, per-block descriptive paragraphs, step-by-step token guides
  for Google Search Console and Bing Webmaster Tools, improved `help=` tooltips with
  format examples, link to Google Rich Results Test validator

### Fixed — JSON-LD rendering (critical)

- **HTML entity encoding of JSON-LD output** (`seo_schema_instance.py`): QWeb's `t-out`
  HTML-escapes plain strings, turning `"` into `&#34;` inside `<script>` tags, which
  breaks JSON parsers and Google's Rich Results validator with "Missing '}'" errors.
  Fixed by returning `markupsafe.Markup(json_str)` from `get_rendered_json_ld()` so
  QWeb emits verbatim content.

- **`sameAs` rendered as bound-method string** (`seo_schema_engine.py`): `_resolve_path`
  used `getattr()` which returns the method object rather than its return value. Fixed
  by detecting callable bound methods at the end of the traversal loop and calling them
  automatically (zero-argument call, with exception guard).

- **`get_era_seo_social_profiles_json` double-encodes** (`res_config_settings.py`):
  method returned `json.dumps([...])` (a JSON string), which `| json` filter then
  serialised again, producing a JSON string rather than an array. Fixed by returning a
  plain Python list so `| json` produces `["url1","url2"]` correctly.

### Fixed — Test suite (Odoo 19 compatibility)

- **`env.sudo()` AttributeError**: Odoo 19 removed `Environment.sudo()`; replaced all
  occurrences in test helpers with `self.env(su=True)`.
- **`t-call="website.layout"` missing in test view**: test page view was a bare
  `<div>` with no layout wrapper, so the `<head>` injection (JSON-LD scripts) never
  occurred; fixed by adding `<t t-call="website.layout">` in the arch.
- **`'"@type"' not found` assertion**: QWeb HTML-encodes `"` as `&#34;` in script
  content; assertions now call `html.unescape(response.text)` before checking.
- **PostgreSQL ABORTED transaction** in `test_duplicate_code_raises`: intentional
  constraint violation left the transaction in ABORTED state, breaking teardown;
  wrapped with `self.env.cr.savepoint()` so the sub-transaction rolls back cleanly.
- All 121 tests pass (`era_seo_manager: 121 tests`).

### Fixed — Phase 2.1 rendering (critical)

- **JSON-LD not emitting on controller-rendered routes** (`website_meta_templates.xml`):
  The `era_seo_schema_ld` template guarded schema fetching with
  `main_object and ...`, which short-circuits to `False` on routes (e.g. `/`)
  where `main_object` is `None`. Fixed by fetching site-wide schemas
  (`res_model='website'`) and page schemas independently, then concatenating;
  empty-recordset fallback (`or env['era.seo.schema.instance']`) ensures
  safe `+` concatenation in all cases.

- **Default schemas attached to `website.page` instead of `website`**
  (`__init__.py` `post_init_hook`, `migrations/19.0.2.1.0/post-migrate.py`):
  Post-init hook was creating instances on the home `website.page` record,
  causing Organization and WebSite schemas to render only on `/`, not site-wide.
  Fixed hook to create instances at `res_model='website'`; migration script
  moves existing misplaced instances to the correct model and removes old ones.

- **`og:locale` meta tag never emits** (`website_layout_templates.xml`):
  `og:locale` was inside the `t-if="seo"` gate in `meta_tags`, making it
  unreachable on non-ERA pages. Moved to a dedicated xpath in the layout
  inject (Step 9) that runs on every page regardless of ERA mode, using
  `request.lang.code` with fallback to `website.default_lang_id.iso_code`.

### Tests

- `test_schema_rendering.py`: added `test_site_schema_renders_on_homepage`
  covering the case where `main_object` is `None` but the website has
  site-level schema instances attached — regression test for Bug 1 above.

## [19.0.2.0.0] — 2026-05-26

### Added — Phase 2 (JSON-LD Schema Engine)
- `era.seo.schema.template` model: reusable JSON-LD templates with placeholder support
- `era.seo.schema.instance` model: per-page attachment of a template to any record
- Placeholder engine (`seo_schema_engine.py`): restricted `{{ dotted.path | filter }}`
  grammar resolved via `getattr` — no `eval`, no third-party templating library
- 17 built-in schema templates: Organization, LocalBusiness, WebSite,
  MobileApplication, SoftwareApplication, Article, BlogPosting, NewsArticle,
  FAQPage, HowTo, BreadcrumbList, Product, Service, Person, Event,
  VideoObject, AggregateRating
- `res.config.settings` ERA SEO section: organization defaults, social profiles,
  site-verification codes, `era_seo_schema_engine_enabled` toggle
- `res.company.get_era_seo_social_profiles_json()` helper for `sameAs` arrays
- Frontend: `era_seo_schema_ld` QWeb template + `website.layout` xpath inject
  emits one `<script type="application/ld+json">` per active instance in sequence order
- Admin UI: Schema Templates tree/form views with Ace JSON editor
- **Preview** wizard: renders any template against a chosen `website.page`, shows
  rendered JSON and validation result
- **Validate JSON-LD** button on `website.page` form opens Google Rich Results Test
- **Schemas** tab on `website.page` backend form for per-page instance management
- Post-init hook: attaches `organization` + `website` schema instances to each
  website's home page on first install
- New tests: `test_schema_engine`, `test_schema_template`, `test_schema_instance`,
  `test_schema_rendering`, `test_builtin_templates`

### Fixed — Phase 1 carry-over
- `seo_security.xml`: migrated to Odoo 19 `res.groups.privilege` API
- `ar.po`: normalised field-reference comment format

## [19.0.1.0.0] — 2026-05-26

### Added — Phase 1 (Core SEO Mixin)
- `era.seo.mixin` abstract model with title, description, OG, Twitter, robots, sitemap, and canonical fields
- `website.page` inherits `era.seo.mixin`; all SEO fields exposed on the page form view
- QWeb `era_seo_manager.meta_tags` template renders the full `<head>` meta block
- `website.layout` xpath inject: title override, canonical override, robots directive, Twitter card
- `post_init_hook`: migrates existing `website_meta_*` values into new ERA SEO fields on install

## Future modules

- `era_seo_gsc_connector` — read-only Google Search Console impressions (v2)

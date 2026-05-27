# Changelog

All notable changes to `era_seo_manager` are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

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

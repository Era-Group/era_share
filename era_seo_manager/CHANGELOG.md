# Changelog

All notable changes to `era_seo_manager` are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

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

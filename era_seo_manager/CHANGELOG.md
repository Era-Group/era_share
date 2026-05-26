# Changelog

All notable changes to `era_seo_manager` are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [Unreleased]

### Added — Phase 1 (Core SEO Mixin)
- `era.seo.mixin` abstract model with title, description, OG, Twitter, robots, sitemap, and canonical fields
- `website.page` inherits `era.seo.mixin`; all SEO fields exposed on the page form view
- QWeb `era_seo_manager.meta_tags` template renders the full `<head>` meta block
- `website.layout` xpath inject: title override, canonical override, robots directive, Twitter card
- `post_init_hook`: migrates existing `website_meta_*` values into new ERA SEO fields on install

## Future modules

- `era_seo_gsc_connector` — read-only Google Search Console impressions (v2)

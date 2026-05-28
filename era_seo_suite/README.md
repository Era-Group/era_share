# ERA SEO Suite — Unified

**Platform:** Odoo 19 · **License:** OPL-1 · **Author:** ERA

The whole SEO suite in one module. Replaces these seven previously
separate addons (now marked `installable: False`):

| Old module | What it contributed |
|---|---|
| `era_seo_manager` | Core SEO mixin, schema engine (17 templates), redirects, sitemap, robots, hreflang, audit dashboard, content blocks |
| `era_seo_ai` | AI auto-fix + proactive Fill SEO via Odoo's AI agent |
| `era_seo_blog` | Blog enhancements: series, categories, authors, FAQ, TOC, related posts, RSS/Atom/JSON feeds |
| `era_seo_blog_ai` | Blog × AI bridge: AI buttons on posts + auto-rebuild on content change |
| `era_geo` | `/llms.txt` + AI crawler control + GEO readiness checks |
| `era_geo_ai` | GEO × AI bridge: AI fix for `geo_no_answer_summary` |
| `era_gsc` | Google Search Console: OAuth, sites, daily analytics pull |

Everything is accessed via a single top-level app **ERA SEO Suite** in the
main menu, with a tabbed Hub: Dashboard / SEO / GEO / GSC / Settings / Guide.

## Install (fresh)

```bash
odoo-bin -i era_seo_suite --stop-after-init
```

## Migrating from the old separate modules

The unified module duplicates the old xmlids under a new prefix
(`era_seo_suite.*` instead of `era_seo_manager.*`, etc.), so the safest
path on an existing database is:

1. Stop the server.
2. From Apps, **uninstall** every old `era_seo_*`, `era_geo*`, `era_gsc`
   module. This drops their tables + xmlids cleanly.
3. **Install** `era_seo_suite`.

Custom records (blog posts, content blocks the admin authored, GSC accounts)
are not noupdate-seeded, so re-creating them after install is required.
Seed data (schema templates, default robots rules, the SEO Fixer AI agent)
is re-seeded by the new module under the new xmlid prefix.

## Code organisation

When multiple old modules had a file with the same name (e.g. four modules
each had `models/res_config_settings.py`), every copy lives under a
`_<tag>` suffix so all of them load:

- `_mgr` → from `era_seo_manager`
- `_ai`  → from `era_seo_ai`
- `_geo` → from `era_geo`
- `_blog` / `_blogai` / `_geoai` / `_gsc` → respective bridges

`models/__init__.py` orders imports so base classes load first (`_mgr` →
`_ai` → `_geo` → `_blog` → bridges → `_gsc`).

## License

OPL-1.

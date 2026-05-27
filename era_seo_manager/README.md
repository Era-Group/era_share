# ERA SEO Manager

**Platform:** Odoo 19 Community / Enterprise
**License:** OPL-1
**Author:** ERA — Excellence Resources Arabia
**Version:** see `__manifest__.py` / `CHANGELOG.md`

A unified, opinionated SEO layer for Odoo 19 websites — the core of the ERA
SEO suite. Full Arabic / RTL support throughout.

## Capabilities

- Per-page `<title>`, meta description, canonical URL, robots directives
- Full Open Graph and Twitter Card meta tags
- JSON-LD schema engine with 17 built-in templates (`era.seo.schema.template`
  / `era.seo.schema.instance`)
- Redirect manager (301/302/307/308/410) with bulk CSV import + 404 log
- Per-language sitemaps + sitemap index, managed from the backend
- `robots.txt` managed from the Odoo backend
- Hreflang automation for multilingual (ar/en) websites
- SEO audit dashboard with 20+ actionable, per-language checks
- **Content blocks + website-builder snippets** (Phase 8): a reusable
  `era.content.block` model and seven draggable **ERA SEO** snippets — FAQ
  (auto `FAQPage` JSON-LD), Breadcrumbs (auto `BreadcrumbList`), CTA, Author
  Box, Related Posts, Feature Grid, Pricing Table.

## The ERA SEO addon family

| Addon | Role | Installs when |
|---|---|---|
| **`era_seo_manager`** | Core (this addon) | always |
| `era_seo_blog` | Blog enhancements (series, TOC, feeds, …) | `website_blog` present |
| `era_seo_ai` | AI auto-fix + proactive SEO fill | Odoo **AI** app present |
| `era_seo_blog_ai` | Blog ↔ AI bridge | both blog + AI present |

The last three `auto_install` when their dependencies are met, so a site
gets exactly the layers it needs.

## Installation

```bash
odoo-bin -c odoo.conf -d <db> -i era_seo_manager --stop-after-init
```

Depends on: `base`, `web`, `website`, `mail`, `portal`. (Blog support lives
in the separate `era_seo_blog` addon — `website_blog` is **not** a core
dependency.)

## Development

See `SPEC.md` for the full specification and `CLAUDE.md` for working
conventions.

```bash
# Run module tests
odoo-bin -c odoo.conf -d test_db -i era_seo_manager \
    --test-enable --test-tags era_seo_manager --stop-after-init

# Upgrade + test the whole suite (Odoo.sh shell helper)
bash ../tools/verify_seo.sh

# Lint
pre-commit run --all-files
```

## Changelog

See `CHANGELOG.md`.

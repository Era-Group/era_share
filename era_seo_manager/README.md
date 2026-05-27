# ERA SEO Manager

**Platform:** Odoo 19 Community / Enterprise
**Version:** 19.0.1.0.0
**License:** OPL-1
**Author:** ERA — Excellence Resources Arabia

A unified, opinionated SEO layer for Odoo 19 websites. Ships the following capabilities:

- Per-page `<title>`, meta description, canonical URL, robots directives
- Full Open Graph and Twitter Card meta tags
- JSON-LD schema engine with 13+ built-in templates
- Redirect manager (301/302/307/308/410) with bulk CSV import
- Per-language sitemaps + sitemap index with admin UI
- `robots.txt` managed from the Odoo backend
- SEO audit dashboard with 22+ actionable checks
- Blog enhancements: reading time, related posts, article series, auto-TOC, author profiles, RSS/Atom/JSON Feed
- Hreflang automation for multilingual (ar/en) websites
- Full Arabic / RTL support

## Installation

```bash
odoo-bin -c odoo.conf -d <db> -i era_seo_manager --stop-after-init
```

Requires: `website`, `website_blog`, `mail`, `portal`.

## Development

See `SPEC.md` for the full specification and `CLAUDE.md` for working conventions.

```bash
# Run module tests
odoo-bin -c odoo.conf -d test_db -i era_seo_manager \
    --test-enable --test-tags era_seo_manager --stop-after-init

# Lint
pre-commit run --all-files
```

## Changelog

See `CHANGELOG.md`.

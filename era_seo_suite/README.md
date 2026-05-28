# `era_seo_suite` — Unified Hub

**Platform:** Odoo 19 · **License:** OPL-1 · **Author:** ERA

One prominent entry point in the main menu (**ERA SEO Suite**) that opens
a single screen with tabs for the whole suite — instead of the scattered
sub-menus under Website → SEO.

## Tabs

| Tab | What it shows |
|---|---|
| **Dashboard** | KPIs: published pages, active redirects, schema instances, last audit + open findings, AI crawlers + /llms.txt status, GSC clicks/impressions (28 days), last GSC pull. |
| **SEO** | Launchpad buttons → Schema Templates, Content Blocks, SEO Overview, Redirects, 404 Log, Hreflang, Audit Runs, All Findings. |
| **GEO** | /llms.txt status, AI crawler counters, button to Manage AI Crawlers. |
| **GSC** | Connected accounts, sites, last pull, 28-day clicks/impressions, buttons to Accounts / Sites / Queries. |
| **Settings** | Inline toggles for the most-used flags (AI auto-fix, /llms.txt, site summary, GSC pull window). Button to the full Website settings page. |
| **Guide** | Collapsed `<details>` walkthroughs: AI first run, GEO setup, GSC connect. |

## Notes

- `era_seo_suite` is `auto_install: True` and `application: True` — it shows
  up as a top-level app whenever all of `era_seo_manager`, `era_geo`, and
  `era_gsc` are installed.
- The existing per-feature menus under Website → SEO continue to work; the
  hub is an additional, faster entry point.
- All Settings-tab toggles round-trip through `ir.config_parameter`, so
  they mean the same thing as the corresponding fields in
  Website → Configuration → Settings.

## License

OPL-1.

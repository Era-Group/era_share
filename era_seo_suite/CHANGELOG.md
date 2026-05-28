# Changelog

## [19.0.1.1.0] — 2026-05-28

### Added — Run Audit Now button on the hub header

A primary button next to **Refresh** that creates an `era.seo.audit.run`,
executes `_run_audit` synchronously, and opens the resulting run form so
the findings are one click away. Confirm dialog warns the run scans every
published page and may take a few seconds.

## [19.0.1.0.0] — 2026-05-28

### Added — unified hub (top-level app)

New module: a single, prominent **ERA SEO Suite** entry in the main menu
that opens one screen with tabs for the whole suite (Dashboard, SEO, GEO,
GSC, Settings, Guide). Replaces the need to hop between scattered sub-
menus under Website → SEO for day-to-day work.

- `era.seo.suite.hub` — singleton hub model (seeded by data) with non-stored
  KPI computes (pages, redirects, schema instances, last audit + open
  findings, AI crawlers, /llms.txt status, GSC accounts/sites/clicks/
  impressions/last pull).
- Form with a notebook of six tabs:
  - **Dashboard** — KPI grid grouped SEO/GEO/GSC.
  - **SEO / GEO / GSC** — launchpad buttons to the existing actions.
  - **Settings** — inline toggles for the most-used flags (AI auto-fix,
    /llms.txt, site summary, GSC pull window) round-tripping through
    `ir.config_parameter`, plus a button to the full Website Settings page.
  - **Guide** — collapsed `<details>` walkthroughs for AI, GEO, GSC.
- Top-level menu (`menu_era_seo_suite_root`) with the server action
  `action_open_era_seo_suite_hub` landing on the singleton record.
- Arabic translation.

### Notes

- `auto_install: True` + `application: True`: appears as a top-level app
  whenever `era_seo_manager`, `era_geo`, and `era_gsc` are all installed.
- The existing per-feature menus under Website → SEO are **not removed** —
  the hub is an additional, faster entry point; admins who prefer the old
  navigation can keep using it.
- No icon shipped yet — a placeholder for `static/description/icon.png` can
  be added later.

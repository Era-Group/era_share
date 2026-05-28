# Changelog

## [19.0.1.3.0] — 2026-05-28

### Added — help texts and inline guides on every Setting

- **Per-field `help`** on every setting — same wording as the original
  per-module panels — so each field shows a `?` tooltip on hover instead
  of just a label.
- **Inline AI note**: when "AI Auto-Fix Enabled" is on, a small note under
  the section explains where to pick / test the agent (the agent picker
  lives in the AI app's settings, which the suite doesn't hard-depend on).
- **GSC setup guide** is back as a collapsed `<details>` directly under
  the GSC settings — same seven-step walkthrough that used to live in
  era_gsc's settings panel, now in context next to the fields it explains.

## [19.0.1.2.0] — 2026-05-28

### Added — every ICP-backed setting in the hub Settings tab + a menu icon

- The **Settings** tab now owns every ICP key the suite manages — 21 fields
  in 6 sections: Organization, Social profiles, Search engine verification,
  AI Auto-Fix, GEO (/llms.txt + AI crawlers), GSC (OAuth + pull window) —
  with the GSC redirect URI rendered as a copy-to-clipboard field.
- One declarative `_SETTING_MAP` drives shared compute/inverse, so adding a
  new setting later is one line.
- Round-trip through `ir.config_parameter` is unchanged — the legacy
  per-module panels under *Website → Configuration → Settings* stay
  in sync (they're the same ICP keys).
- New `_compute_ai_agent_name` shows the currently-bound AI agent's name
  (read-only) when `era_seo_ai` is installed.
- **Menu icon**: a real `static/description/icon.png` (purple/teal
  "SEO · ERA SUITE" mark) wired into the top-level menu via `web_icon`,
  so the app shows up cleanly in the launcher.

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

# Changelog

## [19.0.1.2.0] — 2026-05-28

### Changed — cleaner settings page; setup guide is collapsed

- Settings source text is now **English**; Arabic shows up via `ar.po` like
  the rest of the suite.
- The big numbered checklist is folded into a compact
  `<details><summary>Show setup guide</summary>…</details>` — the page
  shows only the three fields (Redirect URI, Client ID, Client Secret) and
  the Pull Window by default, with the seven-step guide one click away.
- The Redirect URI sits at the top of the OAuth Client setting (with
  `CopyClipboardChar`) so the admin copies it before going to Google.

## [19.0.1.1.0] — 2026-05-28

### Added — on-page setup walkthrough in Settings

The GSC settings block now carries the **one-time Google Cloud setup** as a
numbered checklist in Arabic, with deep links to each Cloud Console section
(Library, Search Console API, OAuth consent, Credentials), so the admin
configures everything without leaving the page.

A new computed field **`era_gsc_redirect_uri`** renders the exact
`Authorized redirect URI` to paste into the OAuth client, derived from
`web.base.url`; it's shown with the `CopyClipboardChar` widget so the admin
copies it in one click. Both client id + secret got placeholders for clarity.

## [19.0.1.0.0] — 2026-05-28

### Added — Google Search Console connector (Phase 1)

- **OAuth 2.0** connect/refresh against Google Search Console (read scope),
  triggered from the `era.gsc.account` form. Tokens stored on the record;
  refresh token is restricted to the `base.group_system` group.
- **Site discovery** after authorization populates `era.gsc.site` records.
- **`era.gsc.query`** — daily search-analytics rows (date, query, clicks,
  impressions, CTR, position) with a unique (site, date, query) constraint
  so re-pulling the same window upserts cleanly.
- **Daily cron** (`era_gsc.cron_pull_gsc`) pulls each active site under
  each connected account; **Pull Now** does it on demand from the site form.
- Backend list/form views, menu under **Website → SEO → GSC**, settings
  block with OAuth client id/secret + pull window.
- Arabic translation.
- Tests with mocked HTTP: connect/refresh, site discovery, search-analytics
  upsert, idempotent re-pull, error path marks the account state, and the
  Google authorize URL contains the right params.

### Notes

- Bring-your-own credentials: the admin configures a Google Cloud OAuth
  client (Search Console API enabled) and adds the `<base>/era_gsc/oauth/
  callback` redirect URI.
- The OAuth callback flow needs interactive browser verification on staging;
  the unit tests cover model + token plumbing, not the round-trip with
  Google.

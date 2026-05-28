# Changelog

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

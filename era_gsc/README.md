# `era_gsc` — Google Search Console connector

**Platform:** Odoo 19 · **License:** OPL-1 · **Author:** ERA

Connects one or more Google accounts to Odoo via OAuth 2.0 and pulls
search-performance data from Google Search Console.

## What you get

- **`era.gsc.account`** — a connected Google account. Click **Connect with
  Google** to run the standard OAuth flow; the access/refresh tokens are
  stored on the record (refresh token in the `base.group_system` group).
- **`era.gsc.site`** — verified GSC properties under each account. After
  authorization the connector discovers them automatically.
- **`era.gsc.query`** — daily search-analytics rows: (site, date, query,
  clicks, impressions, CTR, position). Idempotent upsert.
- **Daily cron** + a manual **Pull Now** button on each site.
- Backend under **Website → SEO → GSC**.

## One-time Google Cloud setup

1. Create / pick a project in Google Cloud Console.
2. **Enable APIs**: *Search Console API*.
3. **OAuth consent screen**: Internal (or External with your domain).
4. **Credentials → Create credentials → OAuth client ID → Web application**.
5. **Authorized redirect URIs** must include:
   `https://<your-odoo-domain>/era_gsc/oauth/callback`
6. Copy the **Client ID** and **Client Secret** into
   **Settings → ERA SEO — Google Search Console**.

## Connect a property

1. **Website → SEO → GSC → Accounts → New** → name it → Save.
2. **Connect with Google** → grant read access to Search Console.
3. After the callback the account state turns **Connected** and the
   connector discovers your GSC properties under the account.
4. Click **Pull Now** on a site (or wait for the daily cron) to fill in the
   queries.

## Install

```bash
odoo-bin -i era_gsc --stop-after-init
```

Depends on `era_seo_manager`. Tests mock `requests` so they run without
network access.

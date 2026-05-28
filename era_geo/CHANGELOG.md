# Changelog

All notable changes to `era_geo` are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [19.0.3.0.0] — 2026-05-28

### Added — citation-ready content fields (Phase 3)

Two new fields land on `era.seo.mixin` so every host model (website.page,
blog.post, era.content.block, blog series/category/author) carries them:

- **`geo_answer_summary`** (Text, translatable) — 1-2 short, quotable
  sentences that AI engines lift verbatim when citing the page.
- **`geo_key_takeaways`** (Html, translatable, sanitized) — a small `<ul>`
  with 3-5 bullet takeaways for AI summary responses.

Wiring:

- **`/llms.txt`** uses `geo_answer_summary` as the per-item description when
  set, falling back to `seo_description` (and `era_excerpt` for blog posts).
- **New audit check `geo_no_answer_summary`** — flags substantial pages
  (≥300 words) with no quotable summary set.
- **AI fill** — `_ai_fill_fields` is extended defensively (uses `getattr`
  on `super`), so the AI generates these GEO fields when `era_seo_ai` is
  installed, and the module loads fine when it isn't.
- A **GEO tab** is added to the content-block form so the fields are
  editable; the website.page and blog.post forms can be extended later via
  small bridges if you want the fields surfaced there too.

Tests cover the audit check (long-page flag, short-page no-flag,
summary-present no-flag) and the `_ai_fill_fields` extension.

## [19.0.2.0.0] — 2026-05-28

### Added — GEO readiness checks in the SEO audit (Phase 2)

The audit run gains three citability checks, shown in the same audit
dashboard:

- **`geo_llms_txt_disabled`** (site) — /llms.txt is off, so AI engines have
  no curated content map.
- **`geo_answer_bots_blocked`** (site) — one or more answer/search crawlers
  (OAI-SearchBot, PerplexityBot, ChatGPT-User, Perplexity-User) are blocked,
  so your pages can't be cited.
- **`geo_no_heading_structure`** (page) — a substantial page (≥150 words)
  with no H2/H3, which AI engines can't chunk/extract well.

Site-level findings attach to the homepage so they fit the per-page finding
model and auto-resolve when fixed. Implemented by inheriting
`era.seo.audit.run._get_check_methods` — no change to `era_seo_manager`.
Tests cover all three (and the no-finding case).

## [19.0.1.0.0] — 2026-05-28

### Added — Generative Engine Optimization (Phase 1)

New module: the AI-answer-engine companion to `era_seo_manager`.

- **`/llms.txt`** and **`/llms-full.txt`** — a Markdown site map for LLMs
  (llmstxt.org convention), built from published pages + blog posts and the
  ERA SEO titles/descriptions. Public, cached 1h. Toggle + summary + item
  cap + include-blog in **Settings → ERA GEO**.
- **`era.geo.ai.crawler`** — registry of known AI crawlers (GPTBot,
  ChatGPT-User, OAI-SearchBot, ClaudeBot, anthropic-ai, Claude-Web,
  PerplexityBot, Perplexity-User, Google-Extended, Applebot-Extended, CCBot,
  Bytespider, Amazonbot, Meta-ExternalAgent, cohere-ai, Diffbot), each with
  an **Allowed** toggle. Seeded `noupdate` and all allowed by default (no
  behavior change on install).
- **robots.txt** is extended (inherits `website.robots`) to emit one
  `User-agent` / `Allow|Disallow` stanza per crawler via
  `era.geo.ai.crawler._robots_block()`.
- Menu under **Website → SEO → GEO (AI Engines)**. Arabic translation.
- Tests: `_robots_block` allow/block + unique user-agent + empty case;
  HttpCase for `/robots.txt` (contains AI stanzas), `/llms.txt` (served,
  text/plain, Markdown heading) and the disabled→404 path.

### Notes

- The `/llms.txt` content and the in-browser robots behavior should be
  spot-checked on staging; unit tests cover the model + endpoint status /
  shape.

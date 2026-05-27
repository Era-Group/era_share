# Changelog

All notable changes to `era_geo` are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

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

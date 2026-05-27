# `era_geo` — Generative Engine Optimization

**Platform:** Odoo 19 Community / Enterprise
**License:** OPL-1
**Author:** ERA — Excellence Resources Arabia

The AI-era companion to `era_seo_manager`. Where SEO optimizes for search
engines, **GEO** optimizes how AI answer engines — ChatGPT, Perplexity,
Google AI Overviews, Claude — discover, crawl, and cite your content.

## What you get

### 1. `/llms.txt`
A Markdown site map served at `/llms.txt` (and `/llms-full.txt`) following
the [llmstxt.org](https://llmstxt.org) convention — a clean, curated list of
your most important pages and posts that LLMs read to understand the site.
Built from your published content and the ERA SEO titles/descriptions.

```
# ERA — Excellence Resources Arabia

> SEO & GEO for the Saudi market

## Pages
- [Cloud Accounting for Saudi SMEs](https://era.net.sa/services): ZATCA-ready …

## Blog
- [Second Blog Article](https://era.net.sa/blog/2): Insights and updates …
```

Toggle it and set the summary under **Website → Configuration → Settings →
ERA GEO**.

### 2. AI crawler control
A registry of known AI/LLM crawlers (`era.geo.ai.crawler`) — GPTBot,
ChatGPT-User, OAI-SearchBot, ClaudeBot, anthropic-ai, PerplexityBot,
Google-Extended, Applebot-Extended, CCBot, Bytespider, … — each with an
**Allowed** toggle. The choices are emitted into `robots.txt` automatically
(one `User-agent` / `Allow|Disallow` stanza per crawler).

Block a *training* bot to keep your content out of model training sets;
allow a *search/answer* bot to let your pages be cited in AI answers. The
seed defaults to allowing everything (no behavior change on install) — flip
the ones you want to block. Manage under **Website → SEO → GEO (AI Engines)
→ AI Crawlers**.

## Install

```bash
odoo-bin -c odoo.conf -d <db> -i era_geo --stop-after-init
```

Depends on `era_seo_manager` (auto-installs alongside it).

## Roadmap

- GEO audit (citability checks: has llms.txt, structured data, Q&A, …).
- Answer-ready content blocks (key takeaways, quotable summaries).

## License

OPL-1.

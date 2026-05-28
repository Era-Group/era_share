# `era_seo_ai`

AI layer for the ERA SEO suite, powered by **Odoo's built-in AI agent**
(the `ai` app in Odoo 19 Enterprise). Two things: it **auto-fixes audit
findings**, and it **proactively fills** the recommended SEO fields on any
SEO-bearing record.

No third-party Python package, no separate API key — the LLM provider,
model, and credentials all come from whatever is configured under
**Settings → AI**. A dedicated **ERA SEO Fixer** agent ships with the addon
(created once, `noupdate`).

## 1. Auto-fix audit findings

| Workflow | Where | Behavior |
|---|---|---|
| One-by-one review | **Suggest Fix (AI)** on a finding | Reads the page, proposes a value, scores confidence. **Apply Fix** writes it. |
| On the audit run | **Suggest Fixes (AI)** / **Auto-Fix (≥0.8)** | Generates / auto-applies high-confidence fixes for the whole run. |
| Trusted batch | Cog → **Suggest + Auto-Apply (≥0.8)** | Auto-applies the confident ones, leaves the rest for review. |
| Audit trail | **SEO Audit → AI Fix Log** | Every call logged: proposal, confidence, agent/model, applied-by/when. |

### What gets fixed

- Text meta — `missing_seo_title`, `title_too_long/short`,
  `missing_meta_description`, `description_too_long/short` (generated
  **per installed language**, written into each translation).
- Slug — `slug_contains_uppercase` (mechanical), `slug_contains_stopwords`,
  `slug_too_long`.
- `missing_og_image` — mechanical: sets the company logo.
- `missing_schema` — the agent picks the best JSON-LD template (from the
  installed allow-list) and attaches an instance.
- `image_missing_alt` — writes alt text and injects it into the content
  images (falls back to a mechanical alt if the agent misbehaves).
- `thin_content` — proposes an HTML block to append (confidence capped so
  it's never auto-applied without review).

## 2. Proactive "AI: Fill SEO"

Because every SEO model inherits `era.seo.mixin`, **AI: Fill SEO** /
**AI: Rewrite SEO** appear on website pages, blog posts, and content blocks:

- **Fill** populates only the empty fields (per language — it checks each
  language's own stored translation, not the source fallback).
- **Rewrite** regenerates them all.
- The field set is extensible: host models add their own via
  `_ai_fill_fields()` (the blog bridge adds `era_subtitle` / `era_excerpt`).

Filling these fields also enriches the rendered JSON-LD, since the schema
templates read the same `seo_*` fields.

## Setup

1. Install + configure the Odoo **AI** app (provider + an agent).
2. Install `era_seo_ai` (it `depends` on `ai`; auto-installs when AI is present).
3. **Website → Configuration → Settings → ERA SEO — AI Auto-Fix**: tick
   **Enable AI Auto-Fix**, pick the **ERA SEO Fixer** agent, click
   **Test Agent**.

## How it talks to the AI

`ai_client.AIClient` calls
`agent.get_direct_response(prompt=..., context_message=...)`. The response
is a list of strings; the client parses a JSON object out of the first,
tolerating code fences and surrounding prose. Per-call context messages
declare the exact output shape and a hard target-language requirement that
overrides the agent's own "match the content language" guidance.

## License

OPL-1.

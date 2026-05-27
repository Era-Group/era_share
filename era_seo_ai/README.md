# `era_seo_ai`

Claude-powered auto-fix for SEO audit findings in `era_seo_manager`. When
the audit flags a missing title, missing meta description, oversized
title/description, or bad URL slug, this addon lets the admin click
**Suggest Fix (AI)** and get a structured proposal back from Claude.

## What you get

| Workflow | Button | Behavior |
|---|---|---|
| One-by-one review | **Suggest Fix (AI)** on a finding | Claude reads the page's content, proposes a value, scores its own confidence. Admin reviews and clicks **Apply Fix** to write. |
| Batch on selected findings | List view → cog menu → **Suggest AI Fix** | Same flow, applied to every selected finding. |
| Trusted batch | Cog menu → **Suggest + Auto-Apply (≥0.8)** | Suggests for all; auto-applies the high-confidence ones; leaves lower-confidence ones for admin review. |
| Audit trail | **SEO Audit → AI Fix Log** | Every call logged with the prompt, the proposal, token usage, and whether prompt caching kicked in. |

## What gets fixed

Currently the AI workflow handles these check codes:

- `missing_seo_title` → generates a 50-60 char title
- `missing_meta_description` → generates a 140-160 char description
- `title_too_long` → shortens to ≤ 60 chars
- `title_too_short` → expands toward 50-60
- `description_too_long` / `description_too_short` → analogous
- `slug_contains_uppercase` → mechanical lowercase (no API call)
- `slug_contains_stopwords` → AI-rewritten slug
- `slug_too_long` → AI-rewritten slug

All other check codes (missing H1, orphan page, missing schema, broken
redirect chain, etc.) require human judgement and remain manual.

## Configuration

**Website → Configuration → Settings → ERA SEO — AI Auto-Fix**

1. Tick **Enable AI Auto-Fix**.
2. Pick a model:
   - **Haiku 4.5** — default. ~$1 / 1M input tokens. Plenty for SEO copy.
   - **Sonnet 4.6** — for nuanced multilingual nuance.
   - **Opus 4.7** — highest quality, ~5× the cost of Haiku.
3. Provide an Anthropic API key by **one** of:
   - Setting `ANTHROPIC_API_KEY` on the Odoo host (**preferred** — per
     CLAUDE.md security playbook §03).
   - Pasting it into **API Key** here (stored in ICP). The field uses
     a `password` widget so it doesn't render in plain text, but it
     is readable by anyone with admin access to System Parameters.
4. Click **Test API Key** to confirm.

## Costs and prompt caching

The system prompt is intentionally ~5K tokens — role, output schema,
brand voice, length rules, and six worked examples. This is above the
4096-token minimum for prompt caching on Haiku/Opus, so:

- **First call in a 5-min window:** ~1.25× the input price for the
  cached portion (cache write).
- **Every subsequent call:** ~10% of the input price for the cached
  portion (cache read).

After the first call, a batch of 100 findings on Haiku 4.5 costs
roughly **$0.01** in API spend, dominated by the per-call user message
+ output tokens. Check the **AI Fix Log** → `cache_read_input_tokens`
column to confirm caching is working in practice.

## Install

```bash
# 1. Install the anthropic SDK on the Odoo host
pip install anthropic

# 2. Set the API key
export ANTHROPIC_API_KEY=sk-ant-...

# 3. Install or upgrade
odoo-bin -c odoo.conf -d <db> -u era_seo_ai --stop-after-init
```

On Odoo.sh, add `anthropic>=0.40` to your repo's top-level
`requirements.txt` and let the next build pick it up; the API key is
configured via the staging/production environment-variable UI.

## License

OPL-1.

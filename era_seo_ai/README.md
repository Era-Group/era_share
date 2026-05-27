# `era_seo_ai`

Auto-fix for SEO audit findings in `era_seo_manager`, powered by **Odoo's
built-in AI agent** (the `ai` app in Odoo 19 Enterprise). When the audit
flags a missing title, missing meta description, oversized title/
description, or bad URL slug, the admin clicks **Suggest Fix (AI)** and the
configured agent proposes a value.

No third-party Python package, no separate API key — the LLM provider,
model, and credentials all come from whatever is configured under
**Settings → AI**.

## What you get

| Workflow | Where | Behavior |
|---|---|---|
| One-by-one review | **Suggest Fix (AI)** on a finding | The agent reads the page content, proposes a value, scores its own confidence. Review, then **Apply Fix** to write. |
| Batch on selected findings | List → cog → **Suggest AI Fix** | Same flow across all selected findings. |
| Trusted batch | Cog → **Suggest + Auto-Apply (≥0.8)** | Suggests for all; auto-applies the high-confidence ones; leaves the rest for review. |
| Audit trail | **SEO Audit → AI Fix Log** | Every call logged with the proposal, confidence, agent/model, and applied-by/when. |

## What gets fixed

- `missing_seo_title` / `title_too_long` / `title_too_short`
- `missing_meta_description` / `description_too_long` / `description_too_short`
- `slug_contains_uppercase` (mechanical lowercase — no AI call)
- `slug_contains_stopwords` / `slug_too_long`

All other check codes (missing H1, orphan page, missing schema, broken
redirect chain, etc.) need human judgement and stay manual.

## Setup

1. Install and configure the Odoo **AI** app (Apps → AI), including a
   provider and at least one agent. A generic agent is fine — this addon
   sends the SEO house-style rules with every request.
2. Install `era_seo_ai` (it `depends` on `ai`).
3. **Website → Configuration → Settings → ERA SEO — AI Auto-Fix**:
   - Tick **Enable AI Auto-Fix**.
   - Pick an **AI Agent** (or leave empty to use the "Ask AI" agent).
   - Click **Test Agent** to confirm.

## How it talks to the AI

`ai_client.AIClient` calls:

```python
agent.get_direct_response(prompt=<per-finding INPUT block>,
                          context_message=<SEO house-style rules>)
```

`get_direct_response` returns a list of strings; the client parses the
first as a JSON object `{proposed_value, explanation, confidence}`,
tolerating code fences and surrounding prose. The agent's own provider /
model / key (set in the AI app) decide cost and quality.

## License

OPL-1.

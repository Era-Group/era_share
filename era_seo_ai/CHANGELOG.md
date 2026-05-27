# Changelog

## [19.0.2.1.0] — 2026-05-27

### Added — dedicated "ERA SEO Fixer" agent

- Ships a purpose-built `ai.agent` (`era_seo_ai.agent_seo`, name
  "ERA SEO Fixer") whose `system_prompt` carries the full SEO craft:
  field-by-field length/keyword rules, Arabic/English language matching,
  Saudi-market keyword guidance, confidence scoring, hard limits, and six
  worked examples (including an Arabic one).
- The agent is loaded with **`noupdate="1"`** — created once on first
  install, never recreated or overwritten on upgrade. The stable XML id
  guarantees no duplicate, and admin edits to the prompt/model survive
  module updates.
- `llm_model` is intentionally omitted so the record installs cleanly on
  any provider (it falls back to the field default; admins retarget it in
  the AI app to match their configured provider).
- `post_init_hook` points `era_seo.ai_agent_id` at this agent **only when
  the setting is still empty**, so a re-install never overrides a
  deliberate admin choice.
- The client's `SEO_CONTEXT` (sent as the agent's `context_message`) is
  trimmed to a compact OUTPUT-CONTRACT reminder, since the deep
  instructions now live in the agent's own `system_prompt`. It still
  carries enough to keep a non-SEO fallback agent producing parseable
  JSON.

## [19.0.2.0.0] — 2026-05-27

### Changed — switched from the Anthropic SDK to Odoo's built-in AI agent

The whole AI layer now goes through Odoo 19 Enterprise's **AI** app
(`ai.agent.get_direct_response`) instead of calling the Anthropic SDK
directly. No more `pip install anthropic`, no second API key to manage —
the provider, model, and key are whatever the admin configured under
**Settings → AI**.

- **Dependency:** `era_seo_ai` now `depends` on the `ai` addon and no
  longer declares `external_dependencies` (`anthropic`). `requirements.txt`
  removed.
- **Settings:** the API-key + model-dropdown fields are replaced by a
  single **AI Agent** picker (`era_seo.ai_agent_id`). Empty falls back to
  the site's "Ask AI" agent. The **Test API Key** button became
  **Test Agent** (round-trips a one-word prompt through the chosen agent).
- **Client:** `ai_client.AIClient` now builds the per-finding prompt and
  passes the SEO house-style rules as the agent's `context_message`
  (extra system context). The response is a list of strings; we parse the
  first one as JSON, tolerating code fences and surrounding prose.
- **Exception:** `AnthropicUnavailable` → `AIUnavailable`.
- **Audit log:** token/cache columns dropped — Odoo's
  `get_direct_response` doesn't surface per-call usage. The log keeps the
  proposal, confidence, agent/model name, applied-by/when, and errors.
- **Model field** `ai_model_used` now stores the agent's `llm_model`
  (e.g. `gpt-4o`, `claude-...`) or "mechanical" for the no-call slug fix.

Behavior, the three actions (Suggest / Apply / Suggest+Auto-Apply), the
auto-fixable check list, and the manager-group gating are unchanged.

### Requirements

- Requires the Odoo **AI** app (Enterprise). On Community the module
  won't install (no `ai` dependency available) — that's intended.

## [19.0.1.0.0] — 2026-05-27

### Added — AI auto-fix for SEO audit findings

- `era.seo.ai.client.AIClient` — Anthropic SDK wrapper that:
  - Reads API key from `era_seo.ai_api_key` ICP first, then
    `ANTHROPIC_API_KEY` env var.
  - Defaults to `claude-haiku-4-5`; admin can upgrade to
    `claude-sonnet-4-6` or `claude-opus-4-7` from settings.
  - Enforces structured JSON output via
    `output_config.format` with a JSON schema (no regex parsing of
    free-form text).
  - Caches the ~5K-token system prompt on every call via
    `cache_control: {type: "ephemeral"}` — after the first call in
    a 5-minute window, the cached prefix costs ~10% of input price.
    Verified via `usage.cache_read_input_tokens`.
  - Retries 429 / 5xx / network errors automatically (3 retries).
- `era.seo.audit.finding` extension — adds `ai_supported` (computed
  from `check_code`), `ai_status`, `ai_proposed_value`,
  `ai_proposed_field`, `ai_confidence`, `ai_explanation`,
  `ai_model_used`, `ai_last_log_id`. Three actions:
  - `action_ai_suggest`: call Claude, store proposal.
  - `action_ai_apply`: write the proposal to the target record and
    mark the finding resolved.
  - `action_ai_suggest_and_apply`: convenience — suggest, then
    auto-apply when `confidence >= 0.8`.
- `era.seo.ai.fix.log` — one row per Claude call. Captures token
  usage (input/output/cache_read/cache_creation), proposed value,
  confidence, applied/by/when. Surfaces under SEO → SEO Audit →
  AI Fix Log; `cache_hit` boolean compute drives a quick "how
  often is the cache helping?" answer.
- Server actions on the `era.seo.audit.finding` list cog menu:
  *Suggest AI Fix*, *Apply AI Fix*, *Suggest + Auto-Apply
  (≥0.8 confidence)*. Bound via `binding_model_id` + `binding_view_types`.
- Settings UI under **Website → Configuration → Settings → ERA
  SEO — AI Auto-Fix**: enable flag, model dropdown, API key field
  (`password` widget), *Test API Key* button.
- Auto-fixable check codes: `missing_seo_title`,
  `missing_meta_description`, `title_too_long`, `title_too_short`,
  `description_too_long`, `description_too_short`,
  `slug_contains_uppercase` (mechanical — no API call),
  `slug_contains_stopwords`, `slug_too_long`. Other check codes
  set `ai_status = 'not_supported'` and hide the buttons.
- `tests/test_ai_workflow.py` — 7 tests with mocked SDK covering
  the mechanical-fix path, the full suggest → apply round trip,
  cache-hit recording, the confidence threshold on auto-apply,
  and failed-call logging.

### Security

- API key field uses the `password` widget so it doesn't appear
  in plain text in the settings form, and admins are nudged in
  the help text to prefer the env-var approach over the ICP.
  ACLs restrict all AI actions to `group_era_seo_manager`.
- Per-finding actions re-check the group server-side before doing
  anything destructive — UI hiding is not enforcement.

### Notes

- Requires the `anthropic` Python package (`>=0.40`). Add to your
  Odoo.sh `requirements.txt` or install via pip before `-u
  era_seo_ai`. Module loads even when the package is missing; the
  admin gets a friendly error from *Test API Key* and the
  *Suggest Fix* buttons before any call is made.

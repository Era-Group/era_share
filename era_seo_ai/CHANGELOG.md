# Changelog

## [19.0.7.0.0] — 2026-05-27

### Added — richer fixes for four more audit findings

The AI workflow now covers four checks it previously marked
"not AI-fixable" — the warnings from the audit dashboard:

- **`missing_og_image`** — *mechanical, no AI call.* Sets the page's OG
  image to the company logo (the safe universal default an admin can
  replace). Confidence 1.0 when a logo exists, so **Auto-Fix (≥0.8)**
  applies it.
- **`missing_schema`** — the agent reads the page and picks the single
  best JSON-LD template from the **installed allow-list** (it can never
  invent a code). Apply attaches one `era.seo.schema.instance` for that
  template, so the page starts emitting structured data. Re-apply is a
  no-op (won't duplicate the instance).
- **`image_missing_alt`** — the agent writes alt text for every `<img>`
  on the page that lacks one (using filename + nearby text as hints, one
  alt per image, decorative images get `""`). Apply injects the `alt`
  attributes into the page content, matching by `src` then document
  order. Content writes preserve XML validity for the QWeb `arch`
  (XML parser) vs. plain HTML fields (HTML parser).
- **`thin_content`** — the agent proposes a small, on-topic HTML block
  (headings + paragraphs, no scripts/styles, no invented facts). Apply
  appends it to the page content. **Confidence is capped at 0.6** so
  "Suggest + Auto-Apply" never silently injects body copy — a human must
  click Apply.

### Changed

- `AIClient.suggest_fix` is now a dispatcher returning a uniform
  `{fix_type, field, translations, proposed_value, payload, explanation,
  confidence, model}` for every fix family. Existing field/slug fixes
  report `fix_type='field'` and are unchanged.
- `era.seo.audit.finding` gains `ai_fix_type` + `ai_fix_payload` (JSON).
  **Apply Fix** dispatches on `ai_fix_type`: write field(s), set the OG
  image, attach a schema instance, inject image alts, or append HTML.
  Old suggestions (no `ai_fix_type`) default to `field` — no migration
  needed.
- `AI_FIXABLE_CODES` extended with the four new codes; the finding form
  shows the `Fix Type`.

### Tests

- `tests/test_rich_fixes.py` — mocked-agent coverage for all four:
  OG-image mechanical path + apply, schema suggest/apply (and rejection
  of an invented template code), image-alt injection on both the HTML
  and QWeb-`arch` content paths (plus the no-images failure), and
  thin-content confidence capping / no-auto-apply / manual append.

### Notes

- Image-alt and thin-content fixes rewrite live page content. Both go
  through the existing manager-group gate and the explicit Apply step,
  and a parse/serialize failure raises cleanly without writing — a
  corrupted page is never persisted.

## [19.0.6.0.0] — 2026-05-27

### Added — AI Suggest / Fix on the Audit Run form

The embedded findings list on an audit run now exposes the AI workflow
directly, so you don't have to open each finding or the standalone list:

- **Header buttons** on the run form (shown when the run has any
  AI-fixable, unresolved finding):
  - **Suggest Fixes (AI)** — generates suggestions for every AI-fixable
    finding in the run (`action_ai_suggest_findings`).
  - **Auto-Fix (≥0.8)** — suggests and auto-applies the high-confidence
    ones (`action_ai_fix_findings`), behind a confirm dialog.
- **Inline row buttons** on each finding in the embedded list — a
  per-row **Suggest** (magic wand) and **Apply** (check), plus the AI
  status badge.
- `era.seo.audit.run.ai_fixable_count` computed field gates the header
  buttons.

## [19.0.5.0.0] — 2026-05-27

### Changed — finding fixes respect the finding's language

`era_seo_manager` 19.0.7.0.0 makes audit findings language-scoped
(`lang_id`). `AIClient.suggest_fix` now honours it: when a finding is
about one language, the fix generates and writes **only that language's**
translation instead of all of them — so fixing a short English description
no longer overwrites a good Arabic one. Findings with no `lang_id`
(language-agnostic, or older data) still fan out to all installed
languages.

## [19.0.4.0.0] — 2026-05-27

### Added — AI suggestions now cover every installed website language

The translatable SEO fields (`seo_title`, `seo_description`,
`seo_og_title`, `seo_og_description`, `seo_keywords`) are generated in
EACH installed website language and written into the matching translation,
so the `/ar/...` version gets Arabic meta and the `/en/...` version gets
English — instead of one detected language for all.

- `AIClient.fill_seo(record, overwrite, lang)` now takes a target language;
  it reads the page signal from `record.with_context(lang=...)` and emits a
  hard "write all output in <language>" instruction in the prompt.
- `era.seo.mixin._ai_fill_seo` loops the record's website languages
  (`_era_hreflang_languages`, falling back to all active langs), calls the
  agent once per language, and writes each result with
  `with_context(lang=...)`. "Fill missing" now checks emptiness
  per-language; "Rewrite all" overwrites every language.
- `AIClient.suggest_fix` returns a `translations` dict `{lang: value}` for
  translatable fields (one agent call per language) and a single value for
  the non-translatable slug. The finding stores it in a new
  `ai_proposed_translations` field; **Apply Fix** writes each language's
  value into its translation. `ai_proposed_value` keeps the default-language
  value for form display.
- The AI Fix Log records the per-language JSON and lists the languages
  covered in the "Field" column.

### Notes

- Cost scales with language count: a fill/suggest on an N-language site is
  N agent calls. The dedicated SEO agent's prompt-side caching still applies.
- The slug (`url`) is generated once — Odoo handles URL translation
  separately from field translation.

## [19.0.3.0.0] — 2026-05-27

### Added — proactive "AI: Fill SEO" across the whole SEO suite

Beyond reactive audit-finding fixes, the AI can now fill the recommended
meta fields on any SEO record in one call. Because every SEO-bearing model
inherits ``era.seo.mixin``, the action lives on the mixin and is available
everywhere — website pages, blog posts, series, categories, authors — and
filling the fields cascades into the rendered JSON-LD (schema templates
read the same ``seo_*`` fields).

- `era.seo.mixin.action_ai_fill_seo()` — fills only the EMPTY fields among
  `seo_title`, `seo_description`, `seo_og_title`, `seo_og_description`,
  `seo_keywords`.
- `era.seo.mixin.action_ai_rewrite_seo()` — regenerates ALL of them.
- `AIClient.fill_seo(record, overwrite)` — one agent call returns all five
  fields plus an explanation and confidence, parsed from a single JSON
  object (`FILL_CONTEXT` describes the multi-field shape; the dedicated
  agent's system prompt still supplies the SEO craft).
- **Action menu bindings**: two server actions ("AI: Fill Missing SEO",
  "AI: Rewrite All SEO") on `website.page`, available from the list and
  form cog menus (single or bulk). The `post_init_hook` additionally binds
  them to `blog.post`, `era.blog.series`, `era.blog.category`, and
  `era.blog.author` **when `era_seo_blog` is installed** — no hard
  dependency, idempotent, so re-runs never duplicate the actions.
- `era.seo.ai.fix.log` gains a `kind` field (`fix` vs `fill`); full-fill
  runs log the complete multi-field JSON, the fields actually written, and
  the applied state. New log filters/group-by for kind.
- Tests (`tests/test_fill_seo.py`): fill-empties-only, no-overwrite,
  rewrite-overwrites, kind=fill logging, bad-JSON-no-write, batch fill,
  and manager-group gating.

### How JSON-LD is covered

No separate JSON-LD step: the schema templates resolve placeholders like
`{{ record.seo_title }}` / `{{ record.seo_description }}` from the very
fields this action fills, so an AI fill immediately improves the
Organization / Article / BlogPosting / WebSite JSON-LD on the page.

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

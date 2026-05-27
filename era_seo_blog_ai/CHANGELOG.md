# Changelog

## [19.0.1.0.0] — 2026-05-27

### Added — blog ↔ AI bridge (auto-installs when both are present)

New glue module between `era_seo_blog` and `era_seo_ai`. Neither parent
depends on the other; this one `auto_install`s only when both are
installed, so the blog/AI matrix stays clean.

- **AI buttons on the blog post SEO tab** — *AI: Fill SEO* (fills empty
  meta only) and *AI: Rewrite SEO* (regenerates all, with confirm). Gated
  to the SEO Manager group; the server-side methods re-check.
- **Auto-rebuild on content change** — `blog.post.write` regenerates ALL
  SEO meta from the new `content` (every installed website language)
  whenever the body is edited, so meta / Open Graph / keywords / JSON-LD
  stay in sync with the content. Per the product decision this is a full
  rewrite (overwrites existing values), not a fill-empty.
  - Only fires when **AI Auto-Fix is enabled** (`era_seo.ai_enabled`).
  - Runs as a system automation (`_era_ai_system`): the content editor
    doesn't need the SEO-Manager group; the admin opted in by enabling AI.
  - Best-effort: a failed / unavailable / slow AI call is caught and
    logged, never blocking the content save.
  - No recursion: the regenerated values are SEO fields (not `content`)
    and the write carries `_era_ai_no_rebuild`.

### Tests

- `tests/test_auto_rebuild.py`: content edit rewrites SEO; no rebuild when
  AI disabled / on non-content writes; a failed AI call never blocks the
  save; the `_era_ai_should_rebuild` gate.

### Caveats

- A content edit triggers one AI call per installed language, **inside the
  save**, and **overwrites** any hand-written SEO on that post. To switch
  to fill-empty-only or an on-demand refresh instead, change
  `blog.post._era_ai_rebuild_seo` (it currently calls `_ai_fill_seo(
  overwrite=True)`).

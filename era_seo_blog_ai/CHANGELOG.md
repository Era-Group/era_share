# Changelog

## [19.0.1.4.0] — 2026-05-28

### Added — Arabic translation (i18n/ar.po)

The SEO-tab AI buttons and the rebuild confirm dialog are now translated.

## [19.0.1.3.0] — 2026-05-27

### Changed — thin-post skip threshold is now words, not characters

The auto-rebuild now skips a content edit when the new body has fewer than
**50 words** of visible text (was 300 characters). `_MIN_CONTENT_WORDS = 50`;
`_era_ai_word_count` replaces `_era_ai_text_len`.

## [19.0.1.2.0] — 2026-05-27

### Changed — AI buttons moved to the top of the blog post form

The *AI: Fill SEO* / *AI: Rewrite SEO* buttons moved out of the SEO tab
into a form **header** (the stock blog.post form has none, so we add one
before the sheet). They're now in the top action bar, visible from any tab.

## [19.0.1.1.0] — 2026-05-27

### Added — blog-specific fields in the AI fill + thin-content skip

- **Blog fields filled too.** `blog.post._ai_fill_fields` (new extension
  point in `era_seo_ai` 19.0.8.0.0) now also fills the blog `era_subtitle`
  and `era_excerpt` — in every installed website language, like the core
  meta. The excerpt feeds the meta-description fallback and the BlogPosting
  JSON-LD, so filling it improves both. Applies to the SEO-tab buttons and
  the auto-rebuild.
- **Skip thin posts.** The auto-rebuild no longer runs when the new content
  has fewer than 300 characters of visible text (`_MIN_CONTENT_CHARS`) —
  too little signal for useful SEO, and it saves the AI call.

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

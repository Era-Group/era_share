# `era_seo_blog_ai`

Glue between `era_seo_blog` and `era_seo_ai`. **Auto-installs** only when
both are present, so neither parent takes a hard dependency on the other
(not every site has a blog; not every site has the AI app).

## What it adds

- **AI buttons on the blog post form header** — *AI: Fill SEO* (fills the
  empty meta only) and *AI: Rewrite SEO* (regenerates them all). Gated to the
  SEO Manager group; the server-side methods re-check.
- **Blog-specific fields in the fill** — `blog.post._ai_fill_fields` extends
  the AI fill set with the blog **Subtitle** (`era_subtitle`) and **Excerpt**
  (`era_excerpt`), generated in every installed website language alongside
  the core meta.
- **Auto-rebuild on content change** — when a post's `content` is edited, all
  SEO meta is regenerated from the new body (full rewrite, every language).
  - Only when **AI Auto-Fix is enabled** (`era_seo.ai_enabled`).
  - Skips thin posts: content with **fewer than 50 words** of visible text
    (`_MIN_CONTENT_WORDS`) does not trigger an AI call.
  - Runs as a system automation (the editor needn't be a SEO manager).
  - Best-effort: a failed/slow AI call never blocks the content save.
  - Non-recursive: it writes SEO fields, not `content`.

## Configuration

To switch the auto-rebuild from full rewrite to fill-empty-only, change
`blog.post._era_ai_rebuild_seo` (it calls `_ai_fill_seo(overwrite=True)`).
The 50-word floor applies to the auto-rebuild only — the manual buttons run
on demand regardless.

## Install

```bash
odoo-bin -c odoo.conf -d <db> -i era_seo_blog_ai --stop-after-init
```

Auto-installs on any DB that has both `era_seo_blog` and `era_seo_ai`.

## License

OPL-1.

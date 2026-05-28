# `era_geo_ai`

Glue between `era_geo` and `era_seo_ai`. **Auto-installs** only when both
are present.

## What it adds

- `geo_no_answer_summary` joins `era_seo_ai.AI_FIXABLE_CODES`, so the
  **Suggest Fix (AI)** / **Apply Fix** buttons appear on the finding and
  the inline row buttons in the audit run.
- The AI client's `_FIELD_MAP` learns the mapping
  `geo_no_answer_summary → geo_answer_summary` (needs_ai=True). The
  existing per-language text-field-fix flow generates a 1-2 sentence
  quotable answer in each installed website language and writes it into
  the page/post/content-block's GEO Answer Summary.

No new agent, no new prompt; the bridge is a small module-level
extension applied at addon load.

## License

OPL-1.

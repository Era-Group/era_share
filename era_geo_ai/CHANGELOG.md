# Changelog

## [19.0.1.0.0] — 2026-05-28

### Added — AI Suggest/Apply for `geo_no_answer_summary`

- `era.seo.audit.finding.ai_supported` is now True for
  `geo_no_answer_summary` (added to `era_seo_ai.AI_FIXABLE_CODES`).
- `era_seo_ai.ai_client._FIELD_MAP` learns
  `geo_no_answer_summary → geo_answer_summary` so the existing per-language
  text-field fix generates a 1-2 sentence quotable answer in each
  installed website language and writes it into the GEO Answer Summary.
- Auto-installs when `era_geo` + `era_seo_ai` are both present.
- Tests: `ai_supported` flips True, suggest stores a proposal targeting
  `geo_answer_summary`, apply writes the value and resolves the finding.

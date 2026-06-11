# Changelog

## [19.0.3.0.0] — 2026-05-28

### Changed — physically merged the entire suite into one module

`era_seo_suite` now contains every model, view, controller, wizard,
template, asset, and migration that used to live in the seven separate
addons. All xmlid prefixes (`era_seo_manager.`, `era_seo_ai.`, `era_geo.`,
`era_gsc.`, `era_seo_blog.`, `era_seo_blog_ai.`, `era_geo_ai.`) have been
rewritten to `era_seo_suite.`; ACL CSVs are merged and deduped; ar.po is
concatenated.

When two modules had a file with the same basename (e.g. four `res_config_
settings.py`, three `seo_mixin.py`), every copy is preserved under a
`_<tag>` suffix (`_mgr`, `_ai`, `_geo`, `_blog`, `_blogai`, `_geoai`,
`_gsc`) and `models/__init__.py` orders imports so base classes load
before extensions.

- 43 model files, 8 controllers, 4 wizards, 23 tests.
- Manifest data: 59 entries (security + data + wizards + views + reports
  + menus, in load order).
- The unified hub form (Dashboard / SEO / GEO / GSC / Settings / Guide)
  remains the single user-facing entry point.

### Decommissioned

The old standalone modules are now `installable: False` and
`auto_install: False`:

    era_seo_manager · era_seo_ai · era_seo_blog · era_seo_blog_ai ·
    era_geo · era_geo_ai · era_gsc

Their code is kept in the repo for history / migration reference; uninstall
them on staging before installing `era_seo_suite`.

## [19.0.2.0.0] — 2026-05-28 (and earlier)

See git log for the per-feature changelog of the soft-consolidation suite
that preceded this physical merge.

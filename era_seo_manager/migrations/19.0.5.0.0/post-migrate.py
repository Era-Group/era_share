"""Post-migration for 19.0.5.0.0 — Phase 6 hreflang backfill.

Iterates every existing website.page and calls _sync_era_hreflang_entries
so the new admin UI is populated immediately after upgrade. Idempotent
and best-effort: a failure on one record is logged and the loop continues.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    from odoo import api, SUPERUSER_ID

    env = api.Environment(cr, SUPERUSER_ID, {})
    pages = env['website.page'].search([])
    if not pages:
        _logger.info('era_seo_manager 19.0.5.0.0: no website pages; nothing to backfill.')
        return

    _logger.info(
        'era_seo_manager 19.0.5.0.0: hreflang backfill for %d website.page records',
        len(pages),
    )
    ok = fail = 0
    for page in pages:
        try:
            page._sync_era_hreflang_entries()
            ok += 1
        except Exception as exc:  # noqa: BLE001
            fail += 1
            _logger.warning(
                'era_seo_manager 19.0.5.0.0: hreflang sync failed for page %d: %s',
                page.id, exc,
            )
    _logger.info(
        'era_seo_manager 19.0.5.0.0: hreflang backfill complete (%d ok, %d failed).',
        ok, fail,
    )

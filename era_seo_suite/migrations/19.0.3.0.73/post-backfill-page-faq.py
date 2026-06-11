"""Backfill the auto-derived FAQPage schema for existing website pages.

The ``page_faq_page`` template and the ``website.page`` auto-attach hooks ship
in 19.0.3.0.73, but pages created before the upgrade never fired the
create/write hooks. Sync them all once so pages that already contain an FAQ
accordion (e.g. the home page) gain their FAQPage instance immediately on
upgrade, with no manual step.

Runs post-migration, i.e. after the new template data has loaded, so
``_sync_era_faq_schema`` can find the ``page_faq_page`` template.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    if 'website.page' not in env or 'era.seo.schema.instance' not in env:
        return
    pages = env['website.page'].sudo().search([])
    if not pages:
        return
    try:
        pages._sync_era_faq_schema()
    except Exception as exc:  # noqa: BLE001
        _logger.warning('era_seo_suite: page FAQ backfill skipped (%s)', exc)
    else:
        _logger.info(
            'era_seo_suite: page FAQ backfill synced %d page(s)', len(pages),
        )

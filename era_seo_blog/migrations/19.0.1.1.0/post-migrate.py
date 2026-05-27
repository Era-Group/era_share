"""Post-migration for 19.0.1.1.0.

post_init_hook only fires on a first install, not on an upgrade. Sites
that already had era_seo_blog 19.0.1.0.0 installed won't get the
BlogPosting / BreadcrumbList / FAQPage instances retroactively attached
without this script.

Iterates over every blog.post and calls _sync_era_default_schemas. Safe
to re-run: the method is idempotent.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    from odoo import api, SUPERUSER_ID

    env = api.Environment(cr, SUPERUSER_ID, {})
    posts = env['blog.post'].search([])
    if not posts:
        _logger.info('era_seo_blog 19.0.1.1.0: no blog posts found, nothing to do.')
        return

    _logger.info(
        'era_seo_blog 19.0.1.1.0: attaching default JSON-LD schemas to '
        '%d existing blog posts', len(posts),
    )
    posts._sync_era_default_schemas()
    _logger.info('era_seo_blog 19.0.1.1.0: schema backfill complete.')

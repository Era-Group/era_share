"""Post-migration for 19.0.1.1.1.

19.0.1.1.0 shipped post_init_hook + a 1.1.0 migration script, but staging
installs that were already at 1.1.0 missed both code paths (post_init_hook
only fires on initial install; 1.1.0 migration only runs on the version
delta into 1.1.0). This script re-runs the backfill so 1.1.0 → 1.1.1
upgrades get the schemas attached.

Idempotent: _sync_era_default_schemas() skips templates already attached.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    from odoo import api, SUPERUSER_ID

    env = api.Environment(cr, SUPERUSER_ID, {})
    posts = env['blog.post'].search([])
    if not posts:
        _logger.info('era_seo_blog 19.0.1.1.1: no blog posts; nothing to backfill.')
        return
    _logger.info(
        'era_seo_blog 19.0.1.1.1: re-syncing default schemas on '
        '%d existing blog posts', len(posts),
    )
    posts._sync_era_default_schemas()

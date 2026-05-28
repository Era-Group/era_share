"""Post-migration for 19.0.2.0.0 — backfill the ERA <-> stock meta sync.

19.0.2.0.0 adds bidirectional sync between the ERA ``seo_*`` fields and the
stock ``website_meta_*`` columns on ``blog.post`` (mirroring website.page).
Existing posts predate the sync, so push each post through it once: ERA wins
where both sides are set; otherwise whichever side has a value fills the other.

Idempotent: the sync only writes when the values actually differ.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    from odoo import api, SUPERUSER_ID

    env = api.Environment(cr, SUPERUSER_ID, {})
    posts = env['blog.post'].search([])
    if not posts:
        _logger.info('era_seo_blog 19.0.2.0.0: no blog posts; nothing to sync.')
        return
    _logger.info(
        'era_seo_blog 19.0.2.0.0: syncing ERA<->stock meta on %d post(s).',
        len(posts),
    )
    for post in posts:
        try:
            post._sync_era_to_stock()
            post._sync_stock_to_era()
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                'era_seo_blog 19.0.2.0.0: meta sync skipped for post %s (%s)',
                post.id, exc,
            )

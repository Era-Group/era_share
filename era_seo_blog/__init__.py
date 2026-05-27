import logging

from . import models, controllers

_logger = logging.getLogger(__name__)


def post_init_hook(env):
    """Backfill auto-attached schemas (BlogPosting, BreadcrumbList,
    optional FAQPage) on every existing blog.post when the addon is
    first installed.
    """
    posts = env['blog.post'].search([])
    if posts:
        _logger.info(
            'era_seo_blog post_init_hook: attaching default schemas to '
            '%d existing blog posts', len(posts),
        )
        posts._sync_era_default_schemas()

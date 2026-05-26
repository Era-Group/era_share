import logging

from . import models, controllers, wizards

_logger = logging.getLogger(__name__)


def post_init_hook(env):
    """Run after the module is first installed.

    Phase 1 task only: copy existing website_meta_* field values into the new
    ERA SEO fields so that pages already configured via the stock SEO popup
    keep their data.  Uses _era_no_sync context flag to prevent the write()
    override in website_page.py from re-syncing ERA → stock (no-op loop).

    Items 2–4 from SPEC §22 (schema defaults, sitemap, robots.txt) are
    deferred to their respective phases.
    """
    _logger.info('era_seo_manager post_init_hook: migrating website_meta_* fields')
    Page = env['website.page'].with_context(_era_no_sync=True)
    pages = Page.search([])
    for page in pages:
        vals = {}
        if not page.seo_title and page.website_meta_title:
            vals['seo_title'] = page.website_meta_title
        if not page.seo_description and page.website_meta_description:
            vals['seo_description'] = page.website_meta_description
        if not page.seo_keywords and page.website_meta_keywords:
            vals['seo_keywords'] = page.website_meta_keywords
        if vals:
            page.with_context(_era_no_sync=True).write(vals)
    _logger.info(
        'era_seo_manager post_init_hook: migrated %d website.page records', len(pages)
    )


def uninstall_hook(env):
    """Run before the module is uninstalled.

    Per SPEC §22: leave SEO data intact so a reinstall does not lose config.
    No destructive action is taken here.
    """
    _logger.info(
        'era_seo_manager uninstall_hook: SEO field data is preserved for reinstall.'
    )

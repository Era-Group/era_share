import logging

from . import models, controllers, wizards

_logger = logging.getLogger(__name__)


def post_init_hook(env):
    """Run after the module is first installed.

    Phase 1 task: copy existing website_meta_* field values into the new ERA
    SEO fields so that pages already configured via the stock SEO popup keep
    their data.

    Phase 2 task: attach the built-in 'organization' and 'website' schema
    templates as active instances on the home page of each website.

    Per SPEC §22.
    """
    # --- Phase 1: migrate website_meta_* → ERA SEO fields -------------------
    _logger.info('era_seo_manager post_init_hook: migrating website_meta_* fields')
    Page = env['website.page'].with_context(_era_no_sync=True)
    pages = Page.search([])
    migrated = 0
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
            migrated += 1
    _logger.info(
        'era_seo_manager post_init_hook: migrated %d website.page records', migrated
    )

    # --- Phase 2: attach default schemas at website level (site-wide) --------
    _logger.info(
        'era_seo_manager post_init_hook: attaching default schema instances to websites'
    )
    default_templates = env['era.seo.schema.template'].search([('is_default', '=', True)])
    if not default_templates:
        _logger.warning(
            'era_seo_manager post_init_hook: no default schema templates found; '
            'skipping schema instance creation.'
        )
        return

    Instance = env['era.seo.schema.instance']
    websites = env['website'].sudo().search([])

    for website in websites:
        for seq, tpl in enumerate(default_templates, start=10):
            # Skip if an instance for this template already exists on this website.
            existing = Instance.sudo().search([
                ('res_model', '=', 'website'),
                ('res_id', '=', website.id),
                ('template_id', '=', tpl.id),
            ], limit=1)
            if existing:
                _logger.debug(
                    'era_seo_manager post_init_hook: instance for %s already exists on '
                    'website %d, skipping.', tpl.code, website.id
                )
                continue

            Instance.sudo().create({
                'template_id': tpl.id,
                'res_model': 'website',
                'res_id': website.id,
                'sequence': seq,
                'active': True,
            })
            _logger.info(
                'era_seo_manager post_init_hook: created %s schema instance on '
                'website %d.', tpl.code, website.id
            )

    _logger.info('era_seo_manager post_init_hook: done.')


def uninstall_hook(env):
    """Run before the module is uninstalled.

    Per SPEC §22: leave SEO data intact so a reinstall does not lose config.
    No destructive action is taken here.
    """
    _logger.info(
        'era_seo_manager uninstall_hook: SEO field data is preserved for reinstall.'
    )

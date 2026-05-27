"""Post-migration for 19.0.2.1.0.

Moves default schema instances from website.page (home page) to website,
so site-wide schemas (Organization, WebSite) are attached at the website
level and rendered on every page via the dual-source schema renderer.

Per SPEC §8.4 / Phase 2.1 fix-up.
"""
import logging

_logger = logging.getLogger(__name__)

# Schema template codes that belong at website level (site-wide).
_SITE_LEVEL_CODES = ('organization', 'website')


def migrate(cr, version):
    from odoo import api, SUPERUSER_ID

    env = api.Environment(cr, SUPERUSER_ID, {})
    Template = env['era.seo.schema.template']
    Instance = env['era.seo.schema.instance']
    websites = env['website'].search([])

    for website in websites:
        for seq, code in enumerate(_SITE_LEVEL_CODES, start=10):
            template = Template.search([('code', '=', code)], limit=1)
            if not template:
                _logger.warning(
                    'era_seo 2.1.0 migration: template "%s" not found, skipping.', code
                )
                continue

            # Create website-level instance if it does not already exist.
            existing_site = Instance.search([
                ('res_model', '=', 'website'),
                ('res_id', '=', website.id),
                ('template_id', '=', template.id),
            ], limit=1)
            if not existing_site:
                Instance.create({
                    'template_id': template.id,
                    'res_model': 'website',
                    'res_id': website.id,
                    'sequence': seq,
                    'active': True,
                })
                _logger.info(
                    'era_seo 2.1.0 migration: created %s instance on website %d.',
                    code, website.id,
                )

            # Remove the old website.page home-page instances for this template.
            # website_id may be NULL (not assigned to a specific website).
            home_pages = env['website.page'].search([
                '|',
                ('website_id', '=', website.id),
                ('website_id', '=', False),
                ('url', '=', '/'),
            ])
            if home_pages:
                old_instances = Instance.search([
                    ('res_model', '=', 'website.page'),
                    ('res_id', 'in', home_pages.ids),
                    ('template_id', '=', template.id),
                ])
                if old_instances:
                    old_instances.unlink()
                    _logger.info(
                        'era_seo 2.1.0 migration: removed %d old website.page %s '
                        'instance(s) for website %d.',
                        len(old_instances), code, website.id,
                    )

    _logger.info('era_seo 2.1.0 migration: done.')

import logging

from odoo import api, models

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# ONDELETE PATTERN FOR POLYMORPHIC SCHEMA INSTANCES
# ---------------------------------------------------------------------------
# era.seo.schema.instance uses a polymorphic (res_model, res_id) reference
# instead of a real FK, so the DB cannot enforce CASCADE.  Every model that
# can host schema instances MUST override unlink() to delete its instances
# first.  Copy the _cleanup_schema_instances() helper below into new host
# models (Phase 5 blog.post, etc.) and call it from unlink().
# ---------------------------------------------------------------------------


class WebsitePage(models.Model):
    """Extend website.page with the ERA SEO mixin.

    Per SPEC §7.2: era.seo.mixin fields land on website.page here.
    Stock website.seo.metadata fields (website_meta_title, etc.) are kept
    intact; our seo_title is treated as authoritative when both are set.
    Sync happens via write() so that the stock QWeb rendering always picks up
    the ERA values without requiring template changes for description/keywords.
    """

    _name = 'website.page'
    _inherit = ['website.page', 'era.seo.mixin']

    # --- Canonical path override ----------------------------------------------

    def _get_seo_path(self):
        return self.url or '/'

    # --- Sync ERA → stock SEO fields -----------------------------------------

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for rec in records:
            rec._sync_era_to_stock()
        return records

    def write(self, vals):
        result = super().write(vals)
        era_keys = {'seo_title', 'seo_description', 'seo_keywords', 'seo_og_image_url'}
        if era_keys.intersection(vals):
            for rec in self:
                rec._sync_era_to_stock()
        return result

    def _sync_era_to_stock(self):
        """Write ERA SEO fields into the delegated website_meta_* columns.

        Called without the sync-skip context so it runs normally through super().
        Skipped when called from the post_init_hook (which sets _era_no_sync).
        """
        if self.env.context.get('_era_no_sync'):
            return
        update = {}
        if self.seo_title and self.website_meta_title != self.seo_title:
            update['website_meta_title'] = self.seo_title
        if self.seo_description and self.website_meta_description != self.seo_description:
            update['website_meta_description'] = self.seo_description
        if self.seo_keywords and self.website_meta_keywords != self.seo_keywords:
            update['website_meta_keywords'] = self.seo_keywords
        if update:
            # Bypass our own write() to avoid recursion; write directly to
            # website.page super (the ORM writes through _inherits to ir.ui.view).
            super(WebsitePage, self).write(update)

    # --- Enrich OG / Twitter via stock get_website_meta() override -----------

    def _default_website_meta(self):
        """Inject ERA OG and Twitter field values into the stock meta defaults.

        The stock QWeb template renders OG / Twitter from the dict returned
        by get_website_meta(), which calls _default_website_meta() internally.
        Overriding here is the correct Odoo hook per the docstring of the
        stock method in website.seo.metadata.
        """
        result = super()._default_website_meta()
        og = result.get('default_opengraph', {})
        tw = result.get('default_twitter', {})

        effective_title = self.seo_og_title or self.seo_title
        if effective_title:
            og['og:title'] = effective_title
            tw['twitter:title'] = effective_title

        effective_desc = self.seo_og_description or self.seo_description
        if effective_desc:
            og['og:description'] = effective_desc
            tw['twitter:description'] = effective_desc

        if self.seo_og_type:
            og['og:type'] = self.seo_og_type

        if self.seo_og_image_url:
            og['og:image'] = self.seo_og_image_url
            tw['twitter:image'] = self.seo_og_image_url

        if self.seo_twitter_card:
            tw['twitter:card'] = self.seo_twitter_card

        if self.seo_twitter_site:
            tw['twitter:site'] = self.seo_twitter_site

        if self.seo_twitter_creator:
            tw['twitter:creator'] = self.seo_twitter_creator

        result['default_opengraph'] = og
        result['default_twitter'] = tw
        return result

    # --- Schema instance cleanup on page delete ------------------------------

    def _cleanup_schema_instances(self):
        """Delete all era.seo.schema.instance records for these pages.

        Called from unlink() before the page records are removed.
        Silently skips if the model is not installed (e.g. during a partial
        test run where era.seo.schema.instance does not yet exist).
        """
        if 'era.seo.schema.instance' not in self.env:
            return
        instances = self.env['era.seo.schema.instance'].sudo().search([
            ('res_model', '=', self._name),
            ('res_id', 'in', self.ids),
        ])
        if instances:
            _logger.debug(
                'website_page.unlink: removing %d schema instance(s) for ids %s',
                len(instances), self.ids,
            )
            instances.unlink()

    def unlink(self):
        """Override to cascade-delete schema instances before the page is removed.

        Per SPEC §8 Step 2: polymorphic FKs have no DB-level CASCADE, so we
        clean up instances here.  See the ONDELETE PATTERN comment at the top
        of this file for how to replicate this in other host models.
        """
        self._cleanup_schema_instances()
        return super().unlink()

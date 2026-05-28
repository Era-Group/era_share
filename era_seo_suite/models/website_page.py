import logging
import urllib.parse

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
            # Seed both directions: ERA wins when both are set, otherwise
            # whichever side carries data populates the other.
            rec._sync_era_to_stock()
            rec._sync_stock_to_era()
        # Phase 6: auto-attach hreflang entries on first creation.
        try:
            records._sync_era_hreflang_entries()
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                'website.page.create: hreflang sync skipped (%s)', exc,
            )
        return records

    # ERA SEO field  <->  stock website.seo.metadata field
    _ERA_TO_STOCK = {
        'seo_title': 'website_meta_title',
        'seo_description': 'website_meta_description',
        'seo_keywords': 'website_meta_keywords',
    }
    _STOCK_TO_ERA = {v: k for k, v in _ERA_TO_STOCK.items()}

    def write(self, vals):
        result = super().write(vals)
        if not self.env.context.get('_era_no_sync'):
            # Bidirectional, last-write-wins. If an ERA field was written
            # (our SEO tab / AI fill), mirror it into the stock website_meta_*
            # columns. If instead a stock field was written (the website
            # builder's "Optimize SEO" dialog), mirror it back into the ERA
            # fields — which is what the frontend actually renders, so the
            # dialog's edits now show up and aren't reverted by a later sync.
            if set(self._ERA_TO_STOCK).intersection(vals):
                for rec in self:
                    rec._sync_era_to_stock()
            elif set(self._STOCK_TO_ERA).intersection(vals):
                for rec in self:
                    rec._sync_stock_to_era()
        # Phase 6: refresh hreflang on URL/website/language-affecting changes.
        hreflang_keys = {'url', 'website_id'}
        if hreflang_keys.intersection(vals):
            try:
                self._sync_era_hreflang_entries()
            except Exception as exc:  # noqa: BLE001
                _logger.warning(
                    'website.page.write: hreflang sync skipped (%s)', exc,
                )
        return result

    def _sync_era_to_stock(self):
        """Mirror ERA SEO fields into the delegated website_meta_* columns.

        Runs in the record's current language context, so per-language ERA
        values land in the matching website_meta_* translation. Skipped under
        the ``_era_no_sync`` guard (post_init_hook, and the reverse sync).
        """
        if self.env.context.get('_era_no_sync'):
            return
        update = {}
        for era_field, stock_field in self._ERA_TO_STOCK.items():
            value = self[era_field]
            if value and self[stock_field] != value:
                update[stock_field] = value
        if update:
            self.with_context(_era_no_sync=True).write(update)

    def _sync_stock_to_era(self):
        """Mirror stock website_meta_* edits (Optimize SEO dialog) into the
        authoritative ERA fields the frontend renders.

        Current-language context, so editing the dialog in Arabic updates the
        Arabic ERA translation. Guarded against recursion via _era_no_sync.
        """
        if self.env.context.get('_era_no_sync'):
            return
        update = {}
        for stock_field, era_field in self._STOCK_TO_ERA.items():
            value = self[stock_field]
            if value and self[era_field] != value:
                update[era_field] = value
        if update:
            self.with_context(_era_no_sync=True).write(update)

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
        """Override to cascade-delete schema instances + hreflang entries.

        Per SPEC §8 Step 2 / §12: polymorphic FKs have no DB-level CASCADE,
        so we clean up both schema instances and hreflang rows here. See the
        ONDELETE PATTERN comment at the top of this file for how to
        replicate this in other host models.
        """
        self._cleanup_schema_instances()
        self._cleanup_era_hreflang()
        return super().unlink()

    # --- Validation helpers --------------------------------------------------

    def action_open_rich_results_test(self):
        """Open Google Rich Results Test pre-filled with this page's URL.

        Returns an ir.actions.act_url so the browser opens a new tab.
        Per SPEC §8 Step 7 (Validate JSON-LD button).
        """
        self.ensure_one()
        base = self.website_id.domain or ''
        base = base.rstrip('/')
        page_url = base + (self.url or '/')
        test_url = (
            'https://search.google.com/test/rich-results?url='
            + urllib.parse.quote(page_url, safe='')
        )
        return {
            'type': 'ir.actions.act_url',
            'url': test_url,
            'target': 'new',
        }

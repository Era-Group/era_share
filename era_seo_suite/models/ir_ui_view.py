"""ERA SEO — ir.ui.view extension.

Website editor content saves write the page's ``ir.ui.view`` (``arch_db``)
directly, not ``website.page``. This override re-syncs the auto-derived
FAQPage schema for any website.page backed by an edited view, so dropping or
removing an FAQ snippet keeps its JSON-LD instance in step.

Guarded to a no-op during module install / upgrade (``registry.ready``) to
avoid churning over every view written while the registry loads, and wrapped
so it can never break a content save.
"""
import logging

from odoo import api, models

_logger = logging.getLogger(__name__)


class IrUiView(models.Model):
    _inherit = 'ir.ui.view'

    def _era_resync_page_faq(self):
        """Re-sync FAQ schema for every website.page backed by these views."""
        if not self or 'website.page' not in self.env:
            return
        try:
            pages = self.env['website.page'].sudo().search(
                [('view_id', 'in', self.ids)]
            )
            if pages:
                pages._sync_era_faq_schema()
        except Exception as exc:  # noqa: BLE001
            _logger.warning('era_seo_suite: page FAQ resync skipped (%s)', exc)

    @api.model_create_multi
    def create(self, vals_list):
        views = super().create(vals_list)
        if self.env.registry.ready:
            views._era_resync_page_faq()
        return views

    def write(self, vals):
        result = super().write(vals)
        if self.env.registry.ready and (
            'arch' in vals or 'arch_db' in vals or 'arch_base' in vals
        ):
            self._era_resync_page_faq()
        return result

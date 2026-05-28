"""ERA SEO — extensions to ``website``.

Re-syncs hreflang rows across every host record when the website's
language settings change. Without this, switching the default language
leaves every page's existing ``era.seo.hreflang`` row pointing at the
old URL prefix.

Per SPEC §12.
"""
import logging

from odoo import models

_logger = logging.getLogger(__name__)


class Website(models.Model):
    _inherit = 'website'

    # The keys that, when written on `website`, must trigger a global
    # hreflang re-sync. Adding/removing a language changes URL alternates;
    # flipping the default lang flips which row is x-default and which
    # path carries the lang prefix.
    _ERA_LANGUAGE_KEYS = ('default_lang_id', 'language_ids')

    def write(self, vals):
        result = super().write(vals)
        if vals and any(k in vals for k in self._ERA_LANGUAGE_KEYS):
            try:
                self.env['era.seo.hreflang']._era_resync_all_records()
            except Exception as exc:  # noqa: BLE001
                _logger.warning(
                    'website.write: hreflang resync skipped (%s)', exc,
                )
        return result

"""Post-migration for 19.0.5.1.0 — full hreflang resync.

Phase 6 (19.0.5.0.0) populated hreflang rows once per record. If the
admin later flipped the website's default language, every URL changed
but the existing rows kept the old prefixes. 19.0.5.1.0 wires a
write hook on `website` that resyncs automatically; this script does
a one-time global resweep so staging gets fixed without having to
re-save each page.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    from odoo import api, SUPERUSER_ID

    env = api.Environment(cr, SUPERUSER_ID, {})
    Hreflang = env['era.seo.hreflang']
    n = Hreflang._era_resync_all_records()
    _logger.info(
        'era_seo_manager 19.0.5.1.0: hreflang resync touched %d records.', n,
    )

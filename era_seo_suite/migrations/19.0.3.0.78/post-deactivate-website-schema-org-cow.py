"""Finish the Organization consolidation: deactivate website_schema_org's
copy-on-write view(s).

The 19.0.3.0.77 migration deactivated only the BASE website_schema_org layout
view (via env.ref). Odoo websites keep a per-website copy-on-write copy with
the same ``key`` but a different xmlid, and that copy is what actually renders
— so the duplicate (``@id``-less) Organization kept emitting. This re-runs the
consolidation, which now deactivates every view by ``key`` (base + COW copies)
and re-asserts the site-wide Organization instance. Idempotent.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    try:
        from odoo.addons.era_seo_suite import _consolidate_organization
    except Exception as exc:  # noqa: BLE001
        _logger.warning('deactivate-cow: could not import helper (%s)', exc)
        return
    _consolidate_organization(env)
    _logger.info('deactivate-cow: organization consolidation re-applied')

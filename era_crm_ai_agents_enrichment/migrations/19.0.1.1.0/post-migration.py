# -*- coding: utf-8 -*-
"""Backfill crm.ai.enrichment.provider.handler_key on upgraded databases.

The provider seed is noupdate="1", so a plain -u adds the new column but never
writes the seeded handler_key values into the existing rows. Without it the
built-in dispatch (which now keys on handler_key) would resolve the seeded
providers to no handler. We set the key ONLY where it is still empty, so a
manager's manual edits are preserved.
"""
import logging

_logger = logging.getLogger(__name__)

# external id (without module prefix) -> handler_key
_KEYS = {
    "provider_wathiq": "wathiq",
    "provider_maroof": "maroof",
    "provider_hunter": "hunter",
    "provider_zerobounce": "zerobounce",
    "provider_hlr": "hlr",
    "provider_whatsapp": "waha",
    "provider_web_search": "websearch",
}


def migrate(cr, version):
    from odoo import api, SUPERUSER_ID
    env = api.Environment(cr, SUPERUSER_ID, {})
    for xmlid, key in _KEYS.items():
        provider = env.ref(
            "era_crm_ai_agents_enrichment.%s" % xmlid, raise_if_not_found=False)
        if provider and not provider.handler_key:
            provider.handler_key = key
            _logger.info("Enrichment: backfilled handler_key=%s on %s", key, xmlid)

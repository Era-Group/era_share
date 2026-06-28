# -*- coding: utf-8 -*-
"""Shared test harness for the Enrichment suite.

Tests are fully OFFLINE: instead of activating the real ZeroBounce/HLR/WAHA
handlers (which would egress), they register FAKE handlers through the engine's
own EXTRA_HANDLERS seam, keyed by ``handler_key``. Each fake returns a canned
uniform result dict, so the waterfall / billing / persistence logic is exercised
deterministically with zero network and zero real cost.
"""
from odoo.tests.common import TransactionCase

from odoo.addons.era_crm_ai_agents_enrichment.services import handlers as H

_NS = "era_crm_ai_agents_enrichment."


class FakeHandler:
    """Base fake — subclasses (built by make_handler) set provider_type + RESULT."""
    provider_type = None
    RESULT = None

    def __init__(self, env, provider):
        self.env = env
        self.provider = provider

    def is_available(self):
        return True

    def capabilities(self):
        return {t.strip() for t in (self.provider.fields_filled or "").split(",")
                if t.strip()}

    def enrich(self, record, fields_needed):
        # Return a fresh copy so the engine never mutates the canned template.
        res = dict(self.RESULT)
        res["data"] = dict(res.get("data") or {})
        res["channel"] = dict(res.get("channel") or {})
        return res


def make_handler(provider_type, result):
    return type("Fake_%s" % provider_type, (FakeHandler,),
                {"provider_type": provider_type, "RESULT": result})


class EnrichmentCase(TransactionCase):
    """Base case: master toggle ON, EXTRA_HANDLERS isolated per test."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.agent = cls.env["crm.ai.enrichment.agent"]
        cls.Provider = cls.env["crm.ai.enrichment.provider"]
        cls.Usage = cls.env["crm.ai.usage"].sudo()
        cls.Audit = cls.env["crm.ai.audit.log"].sudo()
        cls.ICP = cls.env["ir.config_parameter"].sudo()
        cls.agent_rec = cls.env["crm.ai.agent"].search(
            [("tech_name", "=", "era_enrichment")], limit=1)

    def setUp(self):
        super().setUp()
        self.ICP.set_param(_NS + "enabled", "True")
        # Isolate the global handler registry: snapshot + restore around each test.
        self._orig_handlers = dict(H.EXTRA_HANDLERS)
        self.addCleanup(self._restore_handlers)

    def _restore_handlers(self):
        H.EXTRA_HANDLERS.clear()
        H.EXTRA_HANDLERS.update(self._orig_handlers)

    # -- helpers --------------------------------------------------------------
    def register(self, handler_key, provider_type, result):
        H.EXTRA_HANDLERS[handler_key] = make_handler(provider_type, result)

    def make_provider(self, handler_key, provider_type, fields_filled,
                      priority=10, billing_mode="per_call", cost_per_call=0.01):
        return self.Provider.create({
            "name": handler_key,
            "provider_type": provider_type,
            "handler_key": handler_key,
            "fields_filled": fields_filled,
            "priority": priority,
            "billing_mode": billing_mode,
            "cost_per_call": cost_per_call,
            "active": True,
        })

    def make_partner(self, consent=True, **kw):
        vals = {"name": "Enrich Test", "crm_ai_intl_processing_consent": consent}
        vals.update(kw)
        return self.env["res.partner"].create(vals)

    def n_source_rows(self):
        return self.Usage.search_count([("usage_type", "=", "source_api")])

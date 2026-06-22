# -*- coding: utf-8 -*-
"""contact_data handler — decision-maker (individual) sources.

These are the PDPL-heavy sources: named people. The engine only reaches this
handler when the master toggle AND the separate ``fetch_decision_makers`` toggle
are both ON (both default OFF). Most people-search APIs (Apollo, Lusha,
RocketReach, SignalHire) are POST and key off an already-known company domain, so
they fit the 16.5 company-context / a future POST egress rather than blind
discovery; until then they skip honestly. Hunter's domain-search is GET and is
functional once a company domain is in the targeting context. Raw responses go to
the engine's LLM step for structured contact extraction.
"""
import logging

from .base import BaseHandler, register

_logger = logging.getLogger(__name__)


@register("contact_data")
class ContactDataHandler(BaseHandler):
    provider_type = "contact_data"

    def fetch(self, engine, provider, target):
        token = self._token(engine, provider)
        key = (provider.env_key_param or "").upper()
        domain = ((target or {}).get("domain") or "").strip()

        if "HUNTER" in key:
            if not domain:
                _logger.info(
                    "Lead-Gen contact_data: Hunter needs a company domain "
                    "(provided once companies exist, 16.5) — skipping for now.")
                return None
            return engine.http_get(
                "https://api.hunter.io/v2/domain-search",
                params={"domain": domain, "api_key": token, "limit": 10},
            )

        # Apollo / Lusha / RocketReach / SignalHire: POST people-search APIs.
        # The single registered egress seam is GET-only today; broadening it is
        # scoped with the 16.5 company context. Skip honestly, never fake.
        _logger.info(
            "Lead-Gen contact_data: %r needs a POST people-search integration "
            "(scoped with 16.5) — skipping.", provider.name)
        return None

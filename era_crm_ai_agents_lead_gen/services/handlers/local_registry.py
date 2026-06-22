# -*- coding: utf-8 -*-
"""local_registry handler — business-registry lookups (data-residency priority).

OpenCorporates has a documented public API and is fully functional here. The
Saudi registries (Ministry of Commerce / Saudi Business Center) do not publish a
stable open API, so their base URL is read from an env var when an integration is
provisioned; absent that, the handler skips honestly (returns None) and the
waterfall continues — never a fake success. Either way the raw response is handed
to the engine's LLM step for structured extraction.
"""
import logging

from .base import BaseHandler, register

_logger = logging.getLogger(__name__)


@register("local_registry")
class LocalRegistryHandler(BaseHandler):
    provider_type = "local_registry"

    def fetch(self, engine, provider, target):
        token = self._token(engine, provider)
        query = self._query_terms(target) or "company"
        jurisdiction = self._jurisdiction(engine, target)
        key = (provider.env_key_param or "").upper()

        if "OPENCORPORATES" in key:
            params = {"q": query, "api_token": token, "per_page": 20}
            # Only scope by jurisdiction when one is configured; empty => the API
            # searches all jurisdictions (no market hardcoded in code).
            if jurisdiction:
                params["jurisdiction_code"] = jurisdiction
            return engine.http_get(
                "https://api.opencorporates.com/v0.4/companies/search",
                params=params,
            )

        # Saudi MoC / Business Center: base URL provisioned via env when the
        # integration exists. Env var name derives from the token var name.
        if "SAUDI" in key:
            import os
            base_url = os.getenv(key.replace("_KEY", "_URL"), "")
            if not base_url:
                _logger.info(
                    "Lead-Gen local_registry: %s has no base URL configured "
                    "(env %s) — skipping.", provider.name, key.replace("_KEY", "_URL"))
                return None
            params = {"q": query}
            if jurisdiction:
                params["country"] = jurisdiction
            return engine.http_get(
                base_url,
                params=params,
                headers={"Authorization": "Bearer %s" % token} if token else {},
            )

        _logger.info("Lead-Gen local_registry: no mapping for %r — skipping.",
                     provider.name)
        return None

    @staticmethod
    def _jurisdiction(engine, target):
        """Resolve the registry jurisdiction code — fully config-driven.

        No market is hardcoded. Resolution order, all sourced from manager
        config (No-Hardcoded-Policy rule):

        1. The explicit default-jurisdiction parameter
           ``era_crm_ai_agents_lead_gen.default_jurisdiction``, if a manager set
           it (read through the engine's namespace-only config helper).
        2. Otherwise the ``target_regions`` targeting value, used when the
           manager expressed the market as a 2-letter ISO country code.
        3. Otherwise empty — the registry API then searches all jurisdictions.
           Conservative, and crucially contains no embedded market literal or
           keyword map in the handler.
        """
        configured = (engine._cfg("default_jurisdiction", "") or "").strip().lower()
        if configured:
            return configured
        regions = ((target or {}).get("regions") or "").strip().lower()
        if len(regions) == 2 and regions.isalpha():
            return regions
        return ""

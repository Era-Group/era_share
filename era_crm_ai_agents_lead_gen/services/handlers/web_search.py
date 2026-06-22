# -*- coding: utf-8 -*-
"""web_search handler — query a search API, return raw results for extraction.

Functional for the seeded web-search providers (SerpAPI, Brave, Google CSE,
Bing). It builds a query from the targeting config, calls the right endpoint with
the provider's token, and returns the raw response text; the engine's LLM step
normalizes whatever shape comes back into structured company JSON — so this one
handler serves every search provider without per-format parsing.

Endpoints + auth-header names are wire-format technical internals (kept in code
per the No-Hardcoded-Policy rule). An unknown search provider returns None →
the waterfall simply moves on.
"""
import logging
import os

from .base import BaseHandler, register

_logger = logging.getLogger(__name__)


@register("web_search")
class WebSearchHandler(BaseHandler):
    provider_type = "web_search"

    def fetch(self, engine, provider, target):
        token = self._token(engine, provider)
        query = self._query_terms(target) or "B2B companies"
        # Bias the query toward firmographic results worth extracting.
        query = "%s company official website contact" % query

        spec = self._endpoint_for(provider, query, token)
        if not spec:
            _logger.info(
                "Lead-Gen web_search: no endpoint mapping for %r — skipping.",
                provider.name)
            return None
        url, params, headers = spec
        return engine.http_get(url, params=params, headers=headers)

    def _endpoint_for(self, provider, query, token):
        """Resolve (url, params, headers) from which search provider this is.

        Matched on the env-var name the provider declares, so adding a new search
        provider is a data edit + (if its wire format is new) one branch here.
        """
        key = (provider.env_key_param or "").upper()

        if "SERPAPI" in key:
            return (
                "https://serpapi.com/search.json",
                {"q": query, "api_key": token, "engine": "google", "num": 10},
                {},
            )
        if "BRAVE" in key:
            return (
                "https://api.search.brave.com/res/v1/web/search",
                {"q": query, "count": 10},
                {"X-Subscription-Token": token, "Accept": "application/json"},
            )
        if "GOOGLE_CSE" in key:
            # Google Custom Search also needs the search-engine id (cx); it is a
            # config value, env-only like the key, not a secret to store.
            cx = os.getenv("ERA_LEADGEN_GOOGLE_CSE_CX", "")
            return (
                "https://www.googleapis.com/customsearch/v1",
                {"q": query, "key": token, "cx": cx, "num": 10},
                {},
            )
        if "BING" in key:
            return (
                "https://api.bing.microsoft.com/v7.0/search",
                {"q": query, "count": 10},
                {"Ocp-Apim-Subscription-Key": token},
            )
        return None

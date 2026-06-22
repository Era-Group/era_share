# -*- coding: utf-8 -*-
"""web_scrape handler — FRAGILE by nature; isolate the parsing.

Public-page scraping (e.g. a vendor's public customers page) is the most brittle
source: markup changes without notice and pages may block bots. We deliberately
do NOT hand-parse the DOM — that coupling is exactly what breaks. Instead we fetch
the raw HTML and let the engine's LLM step extract structured companies, so a
markup change degrades to "fewer results", never a crash. The page URL is a
manager-configurable env var (no scrape target is hardcoded as policy); absent
it, the handler skips honestly.
"""
import logging

from .base import BaseHandler, register

_logger = logging.getLogger(__name__)

# Cap raw HTML handed to the LLM so one huge page can't blow the token budget.
_MAX_HTML_CHARS = 20000


@register("web_scrape")
class WebScrapeHandler(BaseHandler):
    provider_type = "web_scrape"

    def fetch(self, engine, provider, target):
        import os
        # Scrape target is configured per deployment, env-only; never hardcoded.
        url = os.getenv("ERA_LEADGEN_SCRAPE_URL", "").strip()
        if not url:
            _logger.info(
                "Lead-Gen web_scrape: no ERA_LEADGEN_SCRAPE_URL configured for "
                "%r — skipping (fragile source, opt-in only).", provider.name)
            return None

        raw = engine.http_get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; EraLeadGen/1.0)"},
        )
        if not raw:
            return None
        # Truncate: the LLM only needs a representative slice to extract names.
        return raw[:_MAX_HTML_CHARS]

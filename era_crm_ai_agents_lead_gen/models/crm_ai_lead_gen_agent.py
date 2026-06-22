# -*- coding: utf-8 -*-
"""The Lead-Generation agent — the module's single LLM + egress + cost seam.

This is the FIRST concrete agent in the suite to inherit ``crm.ai.agent.mixin``
(Compliance was a cross-cutting guard, not an agent). It is an AbstractModel: it
stores no rows, it just gives the engine (services/lead_gen_engine.py) a home for
the three things only a model can do well:

1. **LLM extraction** — ``_extract_structured`` runs through the mixin's
   ``_call_llm`` so the AI Compliance Guard governs every prompt (consent, PII
   redaction, cost cap, audit). NEVER build LLMApiService here.
2. **External egress** — ``_http_get`` is the ONE place this module reaches the
   network for non-LLM source calls. It is the single greppable seam registered
   in era_crm_ai_agents_rules.md → "External Network Egress Registry". Handlers
   never import ``requests`` themselves; they call ``engine.http_get`` which lands
   here. Only targeting terms (sectors/regions/titles) ever leave — never a
   partner's personal data (Lead Gen DISCOVERS new records; it does not send
   known PII out).
3. **Cost (Rule 14)** — the LLM-extraction spend is booked automatically by the
   guard inside ``_call_llm`` (usage_type='llm'). The engine books the source-API
   spend of each successful batch as one row (``crm.ai.lead_gen.book_source_cost``,
   usage_type='source_api'), so both halves of a run's cost are recorded at
   batch level. A configurable daily cap gates spend before each batch and at
   each create, and the monthly $/token limit is re-checked before each source
   call.

The crm.ai.agent registry row (tech_name ``era_lead_gen``) is pre-seeded in
data/crm_ai_lead_gen_agent_data.xml so runtime stays read-only for users.
"""
import json
import logging
from urllib.parse import urlsplit

from odoo import _, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# Network timeout for any single source call (technical internal, not policy).
_HTTP_TIMEOUT = 20


def _safe_host(url):
    """scheme://netloc only — never the path, query or fragment.

    Rule 03: query strings can carry the API token (e.g. SerpAPI ``api_key``,
    Google CSE ``key``), and so can a network library's exception message. We
    therefore log ONLY the host, never the full URL or a raw exception string.
    """
    parts = urlsplit(url or "")
    return ("%s://%s" % (parts.scheme, parts.netloc)) if parts.netloc else "<source>"


class CrmAiLeadGenAgent(models.AbstractModel):
    _name = "crm.ai.lead_gen.agent"
    _description = "CRM AI Lead-Generation Agent"
    _inherit = ["crm.ai.agent.mixin"]

    # Stable registry key — matches the seeded crm.ai.agent row.
    _agent_tech_name = "era_lead_gen"

    # ------------------------------------------------------------------
    # Public entrypoint (called by the UI action in 16.9 and the cron in 16.8)
    # ------------------------------------------------------------------
    def run_lead_generation(self, limit=None, unattended=False):
        """Run one prospecting pass and return the structured results.

        Thin wrapper that hands off to the orchestrator. Imported lazily so the
        services package (which imports handler modules) loads only when a run is
        actually triggered — never at module import time.
        """
        from odoo.addons.era_crm_ai_agents_lead_gen.services.lead_gen_engine import (
            LeadGenEngine,
        )
        # The engine now fetches, extracts, de-dups/creates (16.5) and attributes
        # cost per record (16.6) in one per-batch loop, so a single call does the
        # whole pass and returns the creation + cap summary.
        return LeadGenEngine(self, unattended=unattended).run(limit=limit)

    # ------------------------------------------------------------------
    # LLM extraction (runs under the Compliance Guard via the mixin)
    # ------------------------------------------------------------------
    def _extract_structured(self, raw_text, kind="company", record=None,
                            unattended=False):
        """Turn a source's raw response into a list of structured dicts.

        Provider-agnostic: rather than parse every provider's wire format, we
        hand the raw response to the LLM and ask for normalized JSON. This is why
        a single handler can serve SerpAPI, Brave, Google CSE, etc.

        :param kind: 'company' or 'contact' — drives the requested schema.
        :param record: optional record for redaction/consent context.
        :param unattended: True for cron/batch — a guard block then skips-with-
            audit (empty result) instead of raising. Passed explicitly by the
            engine (do NOT infer it from context).
        :returns: list[dict]; [] if nothing could be extracted (never raises on a
            bad model reply — a run must survive a junk response).
        """
        if not (raw_text or "").strip():
            return []

        if kind == "contact":
            schema = ('[{"name": "", "job_title": "", "email": "", "phone": "", '
                      '"linkedin": "", "company": ""}]')
            what = "decision-makers (named individuals)"
        else:
            schema = ('[{"name": "", "website": "", "domain": "", "sector": "", '
                      '"city": "", "country": "", "phone": "", "email": ""}]')
            what = "companies (B2B organizations)"

        system = (
            "You extract structured B2B prospecting data. Return ONLY a JSON "
            "array, no prose, no markdown fences. Use exactly these keys per "
            "object: " + schema + ". Omit objects you cannot ground in the input. "
            "Use empty strings for unknown fields."
        )
        prompt = (
            "From the source data below, extract the %s it mentions.\n\n"
            "SOURCE DATA:\n%s" % (what, raw_text)
        )
        # sensitivity='low' -> cheap model (Rule 14 default). The guard records
        # this LLM call's usage automatically (the LLM half of the cost count).
        reply = self._call_llm(
            prompt, sensitivity="low", system=system, record=record,
            unattended=unattended,
        )
        return self._parse_json_array("".join(reply or []))

    @staticmethod
    def _parse_json_array(text):
        """Best-effort: pull the outermost JSON array out of a model reply."""
        text = (text or "").strip()
        if not text:
            return []
        start, end = text.find("["), text.rfind("]")
        if start == -1 or end == -1 or end <= start:
            return []
        try:
            data = json.loads(text[start:end + 1])
        except (ValueError, TypeError):
            _logger.warning("Lead-Gen: could not parse LLM extraction reply as JSON.")
            return []
        return [d for d in data if isinstance(d, dict)] if isinstance(data, list) else []

    # ------------------------------------------------------------------
    # External egress — the SINGLE registered non-LLM network seam
    # ------------------------------------------------------------------
    def _http_get(self, url, params=None, headers=None, timeout=_HTTP_TIMEOUT):
        """Perform ONE outbound GET for a lead-gen source and return body text.

        Registered in era_crm_ai_agents_rules.md → External Network Egress
        Registry. ``requests`` is imported lazily and ONLY here, so this module
        has exactly one greppable egress point (the future anti-bypass test
        allowlists this seam, like the prayer-times call). Never raises: a dead
        source must let the waterfall fall through to the next provider. Sends
        only the caller-supplied query/params (targeting terms) — never PII.
        """
        host = _safe_host(url)
        try:
            import requests  # lazy: keeps module import side-effect free
        except ImportError:  # pragma: no cover - requests ships with Odoo
            _logger.error("Lead-Gen: 'requests' is unavailable; cannot fetch %s", host)
            return None
        try:
            resp = requests.get(
                url, params=params or {}, headers=headers or {}, timeout=timeout,
            )
            resp.raise_for_status()
            return resp.text
        except Exception as exc:  # noqa: BLE001 - any network error => graceful skip
            # NEVER log the raw exception or full URL: both can contain the token
            # (query-param providers). Host + exception class only (Rule 03).
            reason = type(exc).__name__
            _logger.warning("Lead-Gen: source call to %s failed (%s)", host, reason)
            self._log_critical(
                "source_fetch_failed",
                before={"egress_host": host},
                after={"error": reason},
            )
            return None

    # ------------------------------------------------------------------
    # Cost booking (Rule 14)
    # ------------------------------------------------------------------
    # Source-API cost is booked batch-level by the engine via
    # crm.ai.lead_gen.book_source_cost (usage_type='source_api'); the LLM half is
    # booked by the guard inside _call_llm (usage_type='llm').

    def _ensure_enabled(self):
        """Raise if the agent is paused/capped or over its monthly limit.

        Advisory fast-fail before any expensive prep (the guard is still the
        authoritative enforcer at call time)."""
        if not self._agent_tech_name:
            raise UserError(_("Lead-Gen agent is misconfigured (no tech name)."))
        return self._check_cost_cap()

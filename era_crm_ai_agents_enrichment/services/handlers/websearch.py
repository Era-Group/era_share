# -*- coding: utf-8 -*-
"""Web-search company-data provider — runs the search through the Claude CLI
SUBSCRIPTION (the built-in WebSearch tool), gated by a single manager toggle.

Why this handler differs from the email/phone verifiers
-------------------------------------------------------
The verifiers call third-party HTTP APIs directly (their own egress + their own
token). This handler has NO external token: the live web search is performed by
the agent's own CLI/subscription transport, via Claude's built-in WebSearch tool.
The tool is opted in PER CALL — and only for this one run — by stamping the
generic transport context key ``cli_allowed_tools="WebSearch"`` on the call
(era_ai_accounts reads it and whitelists exactly that tool for the single headless
``-p`` invocation; with the key absent every tool stays disabled, so no other
agent or call is affected). Extraction uses the agent's own transport model
(opus / CLI) — no model string is hardcoded here.

PDPL — why this handler passes ``record=None`` to the LLM
---------------------------------------------------------
It searches PUBLIC business information about a COMPANY using the company's public
trade name (and website, if known). A company trade name is not a natural person's
personal data, so there is nothing to redact or consent-gate. We therefore send
ONLY the public company name/domain and deliberately pass ``record=None`` so the
Compliance guard does not mask the very search term we need. To keep that promise
the handler runs ONLY for company records (or an individual's company parent) and
never sends an individual's name/phone/email. The call is still audited (CTX_AGENT
-> an ``ai_request`` row) and its token consumption is metered by the Base guard as
a CLI (``via_cli``) usage row.

FAIL-CLOSED everywhere:
  * toggle ``…websearch_provider_enabled`` (default OFF) -> is_available() False
    (with an audit-log warning), never an exception.
  * no usable company name -> a documented ``no_data`` attempt, never a crash.
  * unparseable model output -> a documented result with no data, never a corrupt
    write.
"""
import json
import logging
import re

from .base import ProviderHandler

_logger = logging.getLogger(__name__)

_NS = "era_crm_ai_agents_enrichment."
_TOOL = "WebSearch"

# Human-readable hint per known capability token, so the model knows what to
# extract. Unknown tokens fall back to the token itself — no hardcoded schema.
# These are PUBLIC business facts about a COMPANY (the company's own published
# contact details), never an individual's personal data.
_FIELD_HINTS = {
    "name": "official registered company name",
    "website": "official website URL (full https://… address)",
    "email": "the company's official public contact email (e.g. info@domain)",
    "phone": "the company's official public phone / switchboard number, "
             "in international format (+country…)",
    "city": "city of the head office",
    "country_id": "country of the head office (full English country name, "
                  "e.g. 'Saudi Arabia')",
    "industry_id": "primary industry / business sector (short English label, "
                   "e.g. 'Information Technology')",
    # legacy tokens kept so an old fields_filled still maps to a hint
    "sector": "primary industry / business sector",
    "country": "country of the head office",
}

# Relational caps: the model returns a NAME string, which we resolve to a record
# id before handing it to the engine (which writes the m2o by id). Unresolved ->
# the field is silently dropped (never a bad write, never auto-create master data).
_RELATIONAL = {
    "country_id": "res.country",
    "industry_id": "res.partner.industry",
}

_NULLISH = {"", "null", "none", "n/a", "na", "unknown", "not found", "-"}


class WebSearchHandler(ProviderHandler):
    provider_type = "web_search"

    def capabilities(self):
        # The engine keys eligibility on provider.fields_filled; mirror it here.
        raw = self.provider.fields_filled or ""
        return {t.strip() for t in raw.split(",") if t.strip()}

    # -- availability: single manager toggle (fail-closed: unset => OFF) ------
    def _enabled(self):
        icp = self.env["ir.config_parameter"].sudo()
        return (icp.get_param(_NS + "websearch_provider_enabled") or "False") == "True"

    def is_available(self):
        if self._enabled():
            return True
        _logger.warning(
            "Enrichment: web-search provider is OFF "
            "(era_crm_ai_agents_enrichment.websearch_provider_enabled) — skipping.")
        return False

    # -- run ------------------------------------------------------------------
    def enrich(self, record, fields_needed):
        company = self._company_name(record)
        if not company:
            # Documented skip: no business name to search (e.g. a standalone
            # individual). Not billable, not a crash.
            return self._empty("no_data")

        wanted = [c for c in (fields_needed or []) if c]
        if not wanted:
            return self._empty("no_data")

        system, prompt = self._build_prompts(company, self._domain_of(record), wanted)

        try:
            # Opt the WebSearch tool in for THIS call only; record=None so the
            # guard never masks the public company name we are searching for.
            reply = self.env["crm.ai.enrichment.agent"].with_context(
                cli_allowed_tools=_TOOL,
            )._call_llm(prompt, sensitivity="low", system=system,
                        record=None, max_output_tokens=700)
        except Exception as exc:  # noqa: BLE001 — a provider must never kill the run
            _logger.warning("Web-search enrich failed (%s).", type(exc).__name__)
            return self._empty("error")

        data = self._resolve(self._parse(reply, wanted))
        # billable=False: the search runs on the CLI SUBSCRIPTION — there is no
        # external per-call charge. The LLM token consumption is metered by the
        # Base guard as a via_cli usage row; never double-booked as a dollar cost
        # here.
        return {"data": data, "channel": {},
                "status": "success" if data else "no_data", "billable": False}

    # -- helpers --------------------------------------------------------------
    @staticmethod
    def _empty(status):
        return {"data": {}, "channel": {}, "status": status, "billable": False}

    def _resolve(self, raw):
        """Turn model strings into engine-writable values: char fields pass
        through; relational caps (country_id/industry_id) are looked up to an
        existing record id. An unresolved relational value is dropped — never a
        bad write, never auto-created master data."""
        out = {}
        for key, val in (raw or {}).items():
            model = _RELATIONAL.get(key)
            if not model:
                out[key] = val
                continue
            rec = self._lookup(model, val)
            if rec:
                out[key] = rec.id
        return out

    def _lookup(self, model, value):
        """Best-effort name/code lookup of an EXISTING master record (read-only)."""
        Model = self.env[model]
        rec = Model.search([("name", "=ilike", value)], limit=1)
        if not rec and model == "res.country" and len(value) == 2:
            rec = Model.search([("code", "=ilike", value)], limit=1)
        if not rec:
            rec = Model.search([("name", "ilike", value)], limit=1)
        return rec[:1]

    def _partner_of(self, record):
        if record._name == "res.partner":
            return record
        return getattr(record, "partner_id", False)

    def _company_name(self, record):
        """The public COMPANY name to search for, or None (-> documented skip).

        A company record -> its own name; an individual -> the company parent's
        name if any, else its commercial company name. A standalone individual has
        no business name we may safely search."""
        p = self._partner_of(record)
        if not p:
            return None
        if getattr(p, "is_company", False) and p.name:
            return p.name.strip()
        parent = getattr(p, "parent_id", False)
        if parent and getattr(parent, "is_company", False) and parent.name:
            return parent.name.strip()
        commercial = getattr(p, "commercial_company_name", False)
        return commercial.strip() if commercial else None

    def _domain_of(self, record):
        p = self._partner_of(record)
        site = getattr(p, "website", False) if p else False
        return (site or "").strip() or None

    def _build_prompts(self, company, domain, wanted):
        hints = "\n".join("- %s: %s" % (c, _FIELD_HINTS.get(c, c)) for c in wanted)
        ident = "Company name: %s" % company
        if domain:
            ident += "\nKnown website: %s" % domain
        system = (
            "You are a B2B data-research assistant. Use the web search tool to "
            "find CURRENT public facts about the company, and fill in AS MANY of "
            "the requested fields as you can confidently support. PREFER the "
            "company's OWN official website and other authoritative public sources "
            "(official directories, the company's verified profiles). Return ONLY a "
            "single JSON object — no prose, no code fences. Use null for any field "
            "you cannot confirm from a real source. Never invent or guess values."
        )
        prompt = (
            "Run a live web search and extract these fields for the company "
            "below.\n\n%s\n\nReturn STRICT JSON with EXACTLY these keys:\n%s\n\n"
            "Rules: fill every field you can support from a real source (maximise "
            "coverage), but if a value is not confirmable use null; a website must "
            "be a full URL; contact details must be the COMPANY's own official "
            "public ones (not an individual's)." % (ident, hints)
        )
        return system, prompt

    def _parse(self, reply, wanted):
        """Strictly parse the model JSON; return only wanted, non-empty values.

        Any failure -> {} (the engine records no data; never a corrupt write)."""
        text = ""
        if isinstance(reply, (list, tuple)) and reply:
            text = reply[0] if isinstance(reply[0], str) else ""
        elif isinstance(reply, str):
            text = reply
        if not text.strip():
            return {}
        obj = self._extract_json(text)
        if not isinstance(obj, dict):
            return {}
        out = {}
        for key in wanted:
            val = obj.get(key)
            if not isinstance(val, str):
                continue  # ignore null / numbers / nested — only clean strings
            val = val.strip()
            if val and val.lower() not in _NULLISH:
                out[key] = val
        return out

    @staticmethod
    def _extract_json(text):
        """Tolerate code fences / surrounding prose: parse the first {...} block."""
        stripped = re.sub(r"```(?:json)?", "", text).replace("```", "").strip()
        try:
            return json.loads(stripped)
        except Exception:  # noqa: BLE001
            m = re.search(r"\{.*\}", stripped, re.DOTALL)
            if not m:
                return None
            try:
                return json.loads(m.group(0))
            except Exception:  # noqa: BLE001
                return None

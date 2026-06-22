# -*- coding: utf-8 -*-
"""Source-handler base class + registry.

One handler per ``crm.ai.lead_gen.provider.provider_type``. A handler's ONLY job
is to build the request for its kind of source and perform the fetch through the
engine's single registered egress (``engine.http_get``). It returns the RAW
response text; the engine then runs the provider-agnostic LLM extraction. This
split keeps every LLM call and every network call funnelled through the agent
model (guard + egress registry), with handlers holding only wire-format details
(endpoints, param names) — which the No-Hardcoded-Policy rule explicitly leaves
in code as technical internals.
"""

# provider_type -> handler instance. Populated by @register at import time.
HANDLER_REGISTRY = {}


def register(provider_type):
    """Class decorator: register a handler for a provider_type."""
    def _wrap(cls):
        HANDLER_REGISTRY[provider_type] = cls()
        return cls
    return _wrap


def get_handler(provider_type):
    """Return the handler instance for a provider_type, or None if none built."""
    return HANDLER_REGISTRY.get(provider_type)


class BaseHandler:
    """Interface for a source handler.

    Subclasses set ``provider_type`` and implement ``fetch``. They must NEVER
    import ``requests`` or call the network directly — always go through
    ``engine.http_get`` so the one registered egress seam stays the only one.
    """

    provider_type = None

    def fetch(self, engine, provider, target):
        """Fetch raw results for one provider given the targeting ``target``.

        :param engine: the LeadGenEngine (gives ``http_get``, ``env``, ``agent``).
        :param provider: the crm.ai.lead_gen.provider row being tried.
        :param target: dict of targeting terms (sectors/regions/size/titles).
        :returns: raw response text (str) on success, or None to let the
            waterfall fall through to the next provider.
        :raises NotImplementedError: for a handler that is a declared stub
            (e.g. LinkedIn) — the engine catches it and continues, never blocks.
        """
        raise NotImplementedError(
            "Handler for %r is not implemented." % (self.provider_type,)
        )

    @staticmethod
    def extract_kind(provider):
        """Which extraction schema this provider feeds: company vs contact."""
        return "contact" if provider.category == "decision_maker" else "company"

    @staticmethod
    def _token(engine, provider):
        """Resolve the provider's token from the env var it names (never stored).

        Returns the live value or '' — the engine has already confirmed presence
        via token_present, but handlers read it here only at call time.
        """
        import os
        key = (provider.env_key_param or "").strip()
        return (os.getenv(key) or "") if key else ""

    @staticmethod
    def _query_terms(target):
        """Flatten targeting dict into a human search query string."""
        parts = []
        for field in ("sectors", "regions", "company_size", "job_titles"):
            val = (target or {}).get(field)
            if val:
                parts.append(val)
        return " ".join(parts).strip()

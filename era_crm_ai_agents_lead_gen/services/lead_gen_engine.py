# -*- coding: utf-8 -*-
"""LeadGenEngine — orchestrates one prospecting pass.

Responsibilities (16.4 scope):
- Read the manager config (master toggle, targeting, missing-token policy,
  decision-maker toggle).
- Apply the THREE activation gates: master toggle, per-source ``active`` +
  token_present (with the warn/silent policy), and the PDPL decision-maker gate.
- Walk eligible sources by priority and STOP at the first that yields results
  (the waterfall), dispatching to the per-type handler for the egress and to the
  agent for the LLM extraction.
- Book BOTH costs: the source-API call (here) and the LLM extraction (booked by
  the guard inside ``_call_llm``).

Out of 16.4 scope (later tasks): creating/de-duplicating partner records (16.5),
the full cost/audit/daily-cap wiring (16.6), the cron (16.8), the views (16.9).
This engine therefore RETURNS structured dicts; it does not write res.partner.

All network egress goes through ``self.http_get`` → the agent's single
registered ``_http_get`` seam. All config reads go through ``_cfg`` → a narrow,
read-only sudo over the ``era_crm_ai_agents_lead_gen.*`` namespace only
(approved elevation, see era_crm_ai_agents_rules.md).
"""
import logging

from odoo.exceptions import UserError

from .handlers import get_handler

_logger = logging.getLogger(__name__)

PARAM_PREFIX = "era_crm_ai_agents_lead_gen."

# Categories that DISCOVER new records (verification sources validate existing
# data and belong to the enrichment/verify stage, not a discovery pass).
_DISCOVERY_CATEGORIES = ("company", "decision_maker")


class LeadGenEngine:
    def __init__(self, agent, unattended=False):
        # ``agent`` is the crm.ai.lead_gen.agent recordset (mixin holder).
        self.agent = agent
        self.env = agent.env
        self.unattended = unattended
        # Persistence layer (de-dup/create + daily cap + cost attribution).
        self.persist = agent.env["crm.ai.lead_gen"]

    # -- the single registered egress seam (delegates to the agent model) --
    def http_get(self, url, params=None, headers=None):
        return self.agent._http_get(url, params=params, headers=headers)

    # ------------------------------------------------------------------
    # Config (narrow read-only sudo over our own namespace only)
    # ------------------------------------------------------------------
    def _cfg(self, key, default=None):
        return self.env["ir.config_parameter"].sudo().get_param(
            PARAM_PREFIX + key, default)

    def _cfg_bool(self, key):
        # Toggles are stored as the strings 'True'/'False' (see 16.3).
        return str(self._cfg(key, "False")).strip().lower() in ("true", "1")

    def _target(self):
        """Build the targeting dict the handlers query against."""
        return {
            "sectors": self._cfg("target_sectors", "") or "",
            "regions": self._cfg("target_regions", "") or "",
            "company_size": self._cfg("target_company_size", "") or "",
            "job_titles": self._cfg("target_job_titles", "") or "",
        }

    # ------------------------------------------------------------------
    # Activation gate
    # ------------------------------------------------------------------
    def _eligible_providers(self, fetch_decision_makers):
        """Active, token-backed, in-scope sources in waterfall (priority) order.

        Enforces all three gates and honors the warn/silent missing-token policy.
        A source that is active but token-less is SKIPPED; whether that skip is
        audited depends on ``block_source_without_token`` (default 'warn').
        """
        policy = (self._cfg("block_source_without_token", "warn") or "warn").lower()
        Provider = self.env["crm.ai.lead_gen.provider"]
        active = Provider.search([("active", "=", True)], order="priority, name")

        eligible = Provider.browse()
        for provider in active:
            # Gate: only discovery categories; decision-makers need the PDPL toggle.
            if provider.category not in _DISCOVERY_CATEGORIES:
                continue
            if provider.category == "decision_maker" and not fetch_decision_makers:
                _logger.info("Lead-Gen: %r is a decision-maker source but "
                             "fetch_decision_makers is OFF — skipping.", provider.name)
                continue
            # Gate: token present (env-only). Honor the warn/silent policy.
            if not provider.token_present:
                if policy != "silent":
                    _logger.warning(
                        "Lead-Gen: source %r active but token env var %r unset — "
                        "skipping.", provider.name, provider.env_key_param)
                    self.agent._log_critical(
                        "blocked",
                        before="active source, token expected in env var %s"
                               % (provider.env_key_param or "<none>"),
                        after="skipped: token env var not set")
                continue
            eligible |= provider
        return eligible

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------
    def run(self, limit=None):
        """Execute one prospecting pass; return a structured result dict.

        Per batch, in order: PRE-CHECK the daily cap (before any spend) → fetch
        (source) → extract (LLM) → de-dup + create (cap re-checked at each create)
        → attribute the batch's source + LLM cost across the created records. Every
        attempt and outcome is audited (Rule 20).
        """
        if not self._cfg_bool("enabled"):
            _logger.info("Lead-Gen: master toggle OFF — nothing to do.")
            self.agent._log_critical(
                "other", before=None, after={"lead_gen": "skipped: disabled"})
            return {"enabled": False, "providers_tried": [], "capped": False,
                    "companies_created": 0, "companies_matched": 0,
                    "contacts_created": 0, "contacts_matched": 0}

        # Advisory fast-fail if the agent is paused/capped/over its monthly limit.
        # In a cron/unattended run this must NEVER raise — it skips-with-audit so
        # the scheduled job completes cleanly (16.8 contract).
        try:
            self.agent._ensure_enabled()
        except UserError as exc:
            if not self.unattended:
                raise
            self.agent._log_critical(
                "cost_cap_exceeded", before=None,
                after={"lead_gen": "skipped (unattended): agent paused/over limit",
                       "detail": str(exc)})
            return {"enabled": True, "skipped": "agent_unavailable",
                    "providers_tried": [], "capped": True,
                    "companies_created": 0, "companies_matched": 0,
                    "contacts_created": 0, "contacts_matched": 0}

        fetch_dm = self._cfg_bool("fetch_decision_makers")
        target = self._target()
        eligible = self._eligible_providers(fetch_dm)

        self.agent._log_critical(
            "other", before={"target": target},
            after={"lead_gen": "run start", "eligible_sources": eligible.mapped("name")})

        result = {"enabled": True, "providers_tried": eligible.mapped("name"),
                  "capped": False, "companies_created": 0, "companies_matched": 0,
                  "contacts_created": 0, "contacts_matched": 0}

        comp = self._waterfall(
            eligible.filtered(lambda p: p.category == "company"),
            kind="company", target=target, limit=limit)
        result["companies_matched"] = comp["matched"]
        result["capped"] = result["capped"] or comp["capped"]

        con = {"created": self.env["res.partner"], "matched": 0}
        if fetch_dm and not result["capped"]:
            con = self._waterfall(
                eligible.filtered(lambda p: p.category == "decision_maker"),
                kind="contact", target=target, limit=limit)
            result["contacts_matched"] = con["matched"]
            result["capped"] = result["capped"] or con["capped"]

        # Count created records by TYPE across both waterfalls — the contact pass
        # can side-create the company a decision-maker belongs to, which is a
        # created COMPANY, so we classify by is_company rather than by waterfall.
        created = comp["created"] | con["created"]
        result["companies_created"] = len(created.filtered("is_company"))
        result["contacts_created"] = len(created.filtered(lambda p: not p.is_company))

        # Hand-off to Enrichment (#2) is SOFT and dependency-free: every created
        # record already carries the lead-gen tag + x_lead_gen_source stamp
        # (16.5), which is the durable signal Enrichment scans on its own
        # schedule once it is built. No call, no model reference, no coupling —
        # we only record that the records are stamped and ready.
        created_total = result["companies_created"] + result["contacts_created"]
        self.agent._log_critical(
            "other", before=None, after={"lead_gen": "run end", **{
                k: result[k] for k in ("companies_created", "companies_matched",
                                       "contacts_created", "contacts_matched",
                                       "capped")},
                "ready_for_enrichment": created_total,
                "handoff": "records tagged + source-stamped for downstream pickup"})
        return result

    def _waterfall(self, providers, kind, target, limit=None):
        """Try sources by priority; STOP at the first that yields results.

        Returns {created: recordset, matched: int, capped: bool}. Each handler
        failure (stub, network error, empty extraction) is isolated and audited
        so the run survives and falls through to the next source — never a crash,
        never a silent skip.
        """
        empty = self.env["res.partner"]
        for provider in providers:
            # PRE-CHECK both caps BEFORE spending anything on this source. A
            # record that would breach a cap must incur zero source-API and zero
            # LLM cost, so the gate sits ahead of the fetch + extraction calls.
            if self.persist.daily_cap_reached():
                self.persist._audit_cap_hit(kind, 0)
                return {"created": empty, "matched": 0, "capped": True}
            if self._over_cost_cap():
                self.agent._log_critical(
                    "cost_cap_exceeded", before={"kind": kind},
                    after={"reason": "monthly cost/token limit reached before source call"})
                return {"created": empty, "matched": 0, "capped": True}

            handler = get_handler(provider.provider_type)
            if handler is None:
                self._audit_attempt(provider, "skip", "no handler for type")
                continue
            try:
                raw = handler.fetch(self, provider, target)
            except NotImplementedError:
                self._audit_attempt(provider, "skip", "handler not implemented")
                continue
            except Exception:  # noqa: BLE001 - isolate a misbehaving source
                # Never log a raw exception (may carry a token) — class name only.
                self._audit_attempt(provider, "failure", "handler error")
                continue
            if not raw:
                self._audit_attempt(provider, "skip", "no data returned")
                continue

            # Data residency (16.7): data has now crossed to/from this source.
            # Flag it when the source is not an in-region (Saudi-local) one.
            self._flag_residency(provider)

            # The source-API call happened -> book its cost (batch-level). The LLM
            # extraction's cost is booked by the guard inside _call_llm. Both
            # halves are thus recorded (usage_type distinguishes them); we do NOT
            # split/relink per record (cost granularity has no operational value
            # and the splitting churned the usage table + needed an extra sudo).
            self.persist.book_source_cost(provider)
            rows = self.agent._extract_structured(
                raw, kind=kind, unattended=self.unattended)

            if not rows:
                self._audit_attempt(provider, "empty", "fetched but 0 extracted")
                continue

            for row in rows:
                row["_source_provider"] = provider.name
                row["_source_type"] = provider.provider_type
            if limit:
                rows = rows[:limit]

            created, matched, capped = self.persist.create_from_rows(rows, kind)
            self._audit_attempt(
                provider, "success",
                "found=%d created=%d matched=%d" % (len(rows), len(created), matched))
            return {"created": created, "matched": matched, "capped": capped}
        return {"created": empty, "matched": 0, "capped": False}

    def _over_cost_cap(self):
        """True when the agent is over its monthly dollar/token limit (Rule 14)."""
        agent_rec = self.agent._get_agent_record()
        return self.env["crm.ai.usage"]._is_over_limit(agent_rec)

    # ------------------------------------------------------------------
    # Audit helpers
    # ------------------------------------------------------------------
    def _audit_attempt(self, provider, outcome, reason):
        """Audit one fetch attempt (Rule 20) — provider, outcome, reason."""
        _logger.info("Lead-Gen: source %r -> %s (%s)", provider.name, outcome, reason)
        self.agent._log_critical(
            "ai_request" if outcome == "success" else "other",
            before={"source": provider.name, "type": provider.provider_type},
            after={"outcome": outcome, "reason": reason})

    def _flag_residency(self, provider):
        """Audit a flag when data moves via a non-in-region source (16.7).

        In-region = a Saudi-local registry (its token env var is namespaced
        SAUDI). Everything else (foreign search/registry/contact APIs) is a
        cross-border movement worth recording — never silent. Gated by the
        data_residency_flag_egress toggle (default ON).
        """
        if not self._cfg_bool("data_residency_flag_egress"):
            return
        if "SAUDI" in (provider.env_key_param or "").upper():
            return
        self.agent._log_critical(
            "other",
            before={"source": provider.name, "type": provider.provider_type},
            after={"data_residency": "data left region via non-local source"})

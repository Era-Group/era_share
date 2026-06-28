# -*- coding: utf-8 -*-
"""The Enrichment agent — public entrypoint + the SINGLE non-LLM egress seam.

SCAFFOLD (task 2.0): the entrypoint and the egress seam are wired but inert. No
provider issues a call yet; provider logic arrives in later 2.x tasks. The seam
already encodes the Rule-03 discipline learned in Module 16: tokens come from the
environment at call time (never the DB), and an exception is sanitized to host +
class BEFORE any log/audit write — a network library's error string can echo a
full URL incl. a query-string token, so the raw exception never reaches a log.
"""
import logging
from datetime import timedelta
from urllib.parse import urlsplit

from odoo import fields, models

_logger = logging.getLogger(__name__)

_NS = "era_crm_ai_agents_enrichment."


def _safe_host(url):
    """scheme://netloc only — never the path/query (which may carry a token)."""
    parts = urlsplit(url or "")
    return ("%s://%s" % (parts.scheme, parts.netloc)) if parts.netloc else "<provider>"


class CrmAiEnrichmentAgent(models.AbstractModel):
    _name = "crm.ai.enrichment.agent"
    _inherit = ["crm.ai.agent.mixin"]
    _description = "CRM AI Enrichment Agent"
    _agent_tech_name = "era_enrichment"

    # ------------------------------------------------------------------
    # Public entrypoint — other agents (e.g. Dormant Gold #4) call this.
    # ------------------------------------------------------------------
    def enrich(self, record, fields=None, request_type="full", unattended=False):
        """Run one enrichment pass over ``record`` and return a structured result.

        SCAFFOLD: delegates to the engine skeleton, which is inert (master toggle
        OFF by default, no providers, nothing billable). Imported lazily so the
        services package loads only when a run is actually triggered.
        """
        from odoo.addons.era_crm_ai_agents_enrichment.services.enrichment_engine import (
            EnrichmentEngine,
        )
        return EnrichmentEngine(self, unattended=unattended).run(
            record, fields=fields, request_type=request_type)

    # ------------------------------------------------------------------
    # Scheduled batch re-verification of stale records (task 2.6).
    # ------------------------------------------------------------------
    def cron_enrich_stale(self):
        """Enrich a cap-bounded batch of STALE partners on schedule.

        Invoked by the ir.cron under the resolved least-privilege cron user (the
        cron code applies ``with_user(crm.ai.agent._get_cron_run_user())``), never
        as root/superuser. Fully INERT unless the master toggle is ON.

        A partner is "stale" when its deliverability was never checked OR was last
        checked before the staleness window, AND it has a contact method to verify
        (email or phone). Never-checked records are prioritised, then the oldest,
        so a large non-null backlog can't starve fresh contacts. Each record runs
        unattended ``contactability`` enrichment; the loop STOPS the instant the
        Rule-14 cost cap trips (a pre-check here avoids even opening a run once
        capped — the engine enforces the same cap per run as a backstop).
        Returns the number of records processed.
        """
        icp = self.env["ir.config_parameter"].sudo()
        if (icp.get_param(_NS + "enabled") or "False") != "True":
            return 0  # master switch OFF -> fully inert

        batch_size = int(icp.get_param(_NS + "cron_batch_size") or 50)
        staleness_days = int(icp.get_param(_NS + "cron_staleness_days") or 30)
        cutoff = fields.Datetime.now() - timedelta(days=staleness_days)

        Partner = self.env["res.partner"]
        has_contact = ["|", ("email", "!=", False), ("phone", "!=", False)]
        # Never-checked first (prioritised), then the oldest-checked stale ones.
        never = Partner.search(
            [("active", "=", True)] + has_contact
            + [("crm_ai_deliverability_checked", "=", False)],
            order="id asc", limit=batch_size)
        partners = never
        slots = batch_size - len(never)
        if slots > 0:
            partners |= Partner.search(
                [("active", "=", True)] + has_contact
                + [("crm_ai_deliverability_checked", "!=", False),
                   ("crm_ai_deliverability_checked", "<", cutoff)],
                order="crm_ai_deliverability_checked asc", limit=slots)
        if not partners:
            return 0

        agent = self._get_agent_record()
        Usage = self.env["crm.ai.usage"]
        processed = 0
        for partner in partners:
            if Usage._is_over_limit(agent):
                self._log_critical(
                    "cost_cap_exceeded", record=partner,
                    after={"event": "enrichment_cron_capped",
                           "processed": processed, "batch_size": batch_size})
                break
            self.enrich(partner, request_type="contactability", unattended=True)
            processed += 1
        _logger.info(
            "Enrichment cron: processed %s of %s stale partner(s).",
            processed, len(partners))
        return processed

    # ------------------------------------------------------------------
    # The SINGLE non-LLM egress seam (every provider call goes through here).
    # ------------------------------------------------------------------
    def _http_get(self, url, params=None, headers=None, timeout=30):
        """GET via ``requests`` (lazy import), returning the response or None.

        Rule 03: the caller passes the token via ``params``/``headers`` resolved
        from ``os.getenv`` — it is never stored. On failure we log/audit ONLY the
        host + exception class name; never the URL, query string, or raw
        exception text (which could contain the token).
        """
        host = _safe_host(url)
        try:
            import requests  # lazy: the only import site in the module
            resp = requests.get(url, params=params, headers=headers, timeout=timeout)
            resp.raise_for_status()
            return resp
        except Exception as exc:  # noqa: BLE001 — a provider failure must not kill a run
            reason = type(exc).__name__
            _logger.warning(
                "Enrichment: provider call to %s failed (%s)", host, reason)
            self._log_critical(
                "source_fetch_failed",
                before={"egress_host": host}, after={"error": reason})
            return None

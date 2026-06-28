# -*- coding: utf-8 -*-
"""Waterfall enrichment orchestrator (task 2.3).

    run(record)
      └─ master toggle OFF                       -> inert no-op
      └─ compute the still-NEEDED fields/channels for this record + request_type
      └─ open a crm.ai.enrichment.request log row (state=processing)
      └─ for provider in _eligible_providers(...):   # active ∧ handler ∧ available, by priority
             if (provider can fill nothing still needed): skip
             if _over_cap(): state=capped; STOP              # ① cap pre-check (per attempt, Rule 14)
             if _cache_hit(provider, record): skip            # ② idempotency seam
             result = handler.enrich(record, target)          # via each handler's own egress seam
             _book_cost(provider, result, record)             # ③ billing -> crm.ai.usage (per billing_mode)
             apply filled fields (only ones still empty); record channels
             if NOTHING still needed: STOP                    # first success(es) completing the need
      └─ finalize the log + write a masked audit summary

CHAIN-CONTROL vs BILLING — read before "fixing" against the Notion text:
The Notion card says "charge on success only / failed providers are not charged".
That is the *chain-control* rule (a success that completes the need stops the
chain), NOT the billing rule. Billing follows each provider's ``billing_mode``
(the binding decision taken in 2.0): ``per_call`` books on every billable attempt
incl. failures (the API call really cost money), ``per_success`` books only when
data is returned, ``free`` never books. So "failed providers are not charged"
holds for ``per_success``/``free`` providers; a failed ``per_call`` provider IS
charged, by design — never silently under-billed.
"""
import json
import logging

from odoo import fields

from .handlers import get_handler

_logger = logging.getLogger(__name__)

_NS = "era_crm_ai_agents_enrichment."

#: capabilities that are deliverability CHANNELS (verified, not stored as a field).
_CHANNELS = {"whatsapp"}

#: deliverability channel -> res.partner boolean the engine persists it to.
_CHANNEL_TO_FIELD = {
    "email": "email_valid",
    "phone": "mobile_valid",
    "whatsapp": "has_whatsapp",
}


class EnrichmentEngine:
    def __init__(self, agent, unattended=False):
        self.agent = agent
        self.env = agent.env
        self.unattended = unattended

    # -- configuration (sudo config READ only — Base elevation #1 category) ----
    def _cfg(self, key, default=None):
        return self.env["ir.config_parameter"].sudo().get_param(_NS + key, default)

    def _enabled(self):
        return (self._cfg("enabled") or "False") == "True"

    # -- field/channel need resolution ----------------------------------------
    @staticmethod
    def _provider_caps(provider):
        """set[str] of the fields/channels a provider can fill (from fields_filled)."""
        raw = provider.fields_filled or ""
        return {tok.strip() for tok in raw.split(",") if tok.strip()}

    def _fields_needed(self, record, fields, request_type):
        """The set of fields/channels this run should try to obtain.

        - explicit ``fields``      -> exactly those.
        - 'contactability'         -> verify email + phone + whatsapp presence.
        - 'specific' w/o fields    -> nothing (caller must name the fields).
        - 'full'                   -> every field any ACTIVE provider can fill
                                       that the record still LACKS, plus the
                                       presence CHANNELS (always re-checkable).
        """
        if fields:
            return {f for f in fields if f}
        if request_type == "contactability":
            return {"email", "phone", "whatsapp"}
        if request_type == "specific":
            return set()
        # 'full': union of active providers' capabilities, restricted to empties.
        candidates = set()
        for provider in self.env["crm.ai.enrichment.provider"].search(
                [("active", "=", True)]):
            candidates |= self._provider_caps(provider)
        needed = set()
        for cap in candidates:
            if cap in _CHANNELS:
                needed.add(cap)
            elif cap in record._fields and not record[cap]:
                needed.add(cap)
        return needed

    # -- waterfall eligibility (active ∧ handler ∧ available, by priority) -----
    def _eligible_providers(self, needed):
        """Ordered list of (provider, handler, caps) that could fill ``needed``.

        Active providers in priority order whose provider_type has a registered
        handler, whose handler reports available right now (token resolves / WAHA
        ready + gate A on), and whose capabilities overlap the needed set. The
        per-attempt ``needed ∩ caps`` recheck happens in run() as the set shrinks.
        """
        eligible = []
        for provider in self.env["crm.ai.enrichment.provider"].search(
                [("active", "=", True)], order="priority, name"):
            caps = self._provider_caps(provider)
            if not (caps & needed):
                continue
            handler = get_handler(self.env, provider)
            if handler is None:
                # No handler for this provider_type yet (lands in 2.4+).
                continue
            try:
                if not handler.is_available():
                    continue
            except Exception as exc:  # noqa: BLE001 — availability probe must not kill the run
                _logger.warning(
                    "Enrichment: %s availability probe failed (%s); skipping.",
                    provider.name, type(exc).__name__)
                continue
            eligible.append((provider, handler, caps))
        return eligible

    def _over_cap(self):
        """① Per-attempt cap pre-check via the Base (Rule 14, dollar path)."""
        agent = self.agent._get_agent_record()
        return self.env["crm.ai.usage"]._is_over_limit(agent)

    def _cache_hit(self, provider, record):
        """② Idempotency seam: skip (and don't re-bill) a recent identical check.

        Wired but inert in 2.3 — a real value-level cache (respecting each
        provider's cache_ttl_days) is a later task; returning False here means
        every eligible provider is attempted.
        """
        return False

    # -- billing (③) — book a BILLABLE source call to crm.ai.usage -------------
    def _book_cost(self, provider, result, record):
        """Book one source_api usage row per the provider's billing_mode.

        free            -> never books.
        not billable    -> never books (no real API call happened).
        per_success     -> books only when the call returned data (status success).
        per_call        -> books on every billable attempt (success OR failure).
        Returns the dollar amount booked (0.0 if nothing was booked).
        """
        mode = provider.billing_mode
        if mode == "free" or not result.get("billable"):
            return 0.0
        if mode == "per_success" and result.get("status") != "success":
            return 0.0
        cost = provider.cost_per_call or 0.0
        agent = self.agent._get_agent_record()
        # record() sudo-creates but captures the caller's uid, so the per-user
        # usage record rule still attributes this source call correctly.
        self.env["crm.ai.usage"].record(
            agent, None, 0, 0, cost,
            usage_type="source_api", record_ref=record)
        return cost

    # -- log + audit ----------------------------------------------------------
    def _open_request(self, record, request_type):
        """Create the per-run log row as the CALLING user (create_uid drives the
        per-user record rule)."""
        vals = {"request_type": request_type, "state": "processing"}
        if record._name == "res.partner":
            vals["partner_id"] = record.id
        elif record._name == "crm.lead":
            vals["lead_id"] = record.id
            if record.partner_id:
                vals["partner_id"] = record.partner_id.id
        return self.env["crm.ai.enrichment.request"].create(vals)

    def _finalize(self, log, state, tried, enriched, contactability, cost):
        log.write({
            "state": state,
            "providers_tried": json.dumps(tried, ensure_ascii=False),
            "fields_enriched": json.dumps(enriched, ensure_ascii=False),
            "contactability": json.dumps(contactability, ensure_ascii=False),
            "total_cost": cost,
        })

    def _audit_run(self, record, state, enriched, tried, cost):
        """Masked, OPERATION-LEVEL run summary (Rule 20). Stores field NAMES + a
        per-provider {provider, status, billable, cost} breakdown + the run total
        — never an enriched VALUE; the subject is referenced by id only."""
        self.agent._log_critical(
            "other", record=record,
            after={
                "event": "enrichment_run",
                "state": state,
                "fields_enriched": sorted(enriched.keys()),
                "operations": [
                    {"provider": t["provider"], "status": t["status"],
                     "billable": t["billable"], "cost": t["cost"]}
                    for t in tried
                ],
                "total_cost": cost,
            })

    def _audit_cap(self, record, agent, tried, cost):
        """Dedicated Rule-14/20 audit when the hard cost cap stops a run. Uses the
        base 'cost_cap_exceeded' event type so it surfaces alongside the LLM-path
        cap events in the same audit stream."""
        self.agent._log_critical(
            "cost_cap_exceeded", record=record,
            after={
                "event": "enrichment_capped",
                "agent": agent.tech_name,
                "month_cost": self.env["crm.ai.usage"]._current_month_cost(agent),
                "cap": self.env["crm.ai.usage"]._get_cap(agent),
                "operations_before_cap": len(tried),
                "run_cost": cost,
            })

    # -- field application ----------------------------------------------------
    def _apply(self, record, data, remaining):
        """Write only still-needed, currently-EMPTY, real model fields. Returns
        the list of field names actually filled (the waterfall never overwrites
        an existing value)."""
        filled, to_write = [], {}
        for field, value in (data or {}).items():
            if field not in remaining or field in _CHANNELS:
                continue
            if field in record._fields and value and not record[field]:
                to_write[field] = value
                filled.append(field)
        if to_write:
            record.write(to_write)
        return filled

    def _persist_deliverability(self, record, contactability):
        """Write DEFINITE (True/False) channel verdicts onto the partner's
        deliverability booleans + the last-checked stamp. An 'unknown' (None)
        verdict is skipped, leaving any prior flag untouched."""
        partner = record if record._name == "res.partner" \
            else getattr(record, "partner_id", False)
        if not partner:
            return
        vals = {}
        for channel, field in _CHANNEL_TO_FIELD.items():
            verdict = contactability.get(channel)
            if verdict is not None:
                vals[field] = bool(verdict)
        if vals:
            vals["crm_ai_deliverability_checked"] = fields.Datetime.now()
            partner.write(vals)

    # -- public entrypoint ----------------------------------------------------
    def run(self, record, fields=None, request_type="full"):
        result = {"enabled": False, "enriched": {}, "providers_tried": [],
                  "contactability": {}, "cost": 0.0, "state": "queued",
                  "request_id": False}
        if not self._enabled():
            return result
        result["enabled"] = True

        needed = self._fields_needed(record, fields, request_type)
        log = self._open_request(record, request_type)
        result["request_id"] = log.id

        remaining = set(needed)
        tried, enriched, contactability, total_cost = [], {}, {}, 0.0

        if not remaining:
            # Nothing to enrich — a clean, charge-free completion.
            self._finalize(log, "done", tried, enriched, contactability, 0.0)
            result.update(state="done")
            return result

        capped = False
        for provider, handler, caps in self._eligible_providers(needed):
            target = caps & remaining
            if not target:
                continue  # lower-priority provider for an already-filled field
            if self._over_cap():
                capped = True
                break
            if self._cache_hit(provider, record):
                continue
            try:
                res = handler.enrich(record, target) or {}
            except Exception as exc:  # noqa: BLE001 — a provider must never kill the run
                _logger.warning(
                    "Enrichment provider %s raised (%s).",
                    provider.name, type(exc).__name__)
                res = {"data": {}, "channel": {}, "status": "error",
                       "billable": False}

            cost = self._book_cost(provider, res, record)
            total_cost += cost

            filled = self._apply(record, res.get("data"), remaining)
            for field in filled:
                enriched[field] = provider.name
            # presence/deliverability channels (whatsapp True/False/None, ...)
            for channel, val in (res.get("channel") or {}).items():
                contactability[channel] = val
                if channel in remaining and val is not None:
                    filled.append(channel)
            remaining -= set(filled)

            tried.append({
                "provider": provider.name,
                "type": provider.provider_type,
                "status": res.get("status"),
                "billable": bool(res.get("billable")),
                "cost": cost,
                "filled": filled,
            })
            # Per-attempt log row — the queryable substrate for the per-provider
            # success-rate/cost dashboard (created as the calling user).
            self.env["crm.ai.enrichment.attempt"].create({
                "request_id": log.id,
                "provider_id": provider.id,
                "provider_name": provider.name,
                "provider_type": provider.provider_type,
                "status": res.get("status") or "",
                "success": res.get("status") == "success",
                "billable": bool(res.get("billable")),
                "cost": cost,
            })

            if not remaining:
                break  # the need is met — first success(es) stop the chain

        # Persist deliverability verdicts to the partner (definite results only).
        self._persist_deliverability(record, contactability)

        if capped:
            state = "capped"
        elif remaining and not enriched and not contactability:
            state = "failed"  # nothing obtained at all
        else:
            state = "done"

        self._finalize(log, state, tried, enriched, contactability, total_cost)
        if tried or capped:
            self._audit_run(record, state, enriched, tried, total_cost)
        if capped:
            self._audit_cap(
                record, self.agent._get_agent_record(), tried, total_cost)

        result.update(enriched=enriched, providers_tried=tried,
                      contactability=contactability, cost=total_cost,
                      state=state)
        return result

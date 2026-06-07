# -*- coding: utf-8 -*-
"""AI Compliance Guard — the single enforcement point for all native Odoo AI.

Architecture decision (final): every LLM call flows through Odoo 19's native AI
(`odoo.addons.ai`). We do NOT add a provider and we do NOT call providers
ourselves. Instead this module *injects* our compliance guarantees by
monkeypatching the native, record-blind egress methods on the plain
``LLMApiService`` class, plus one record-aware entry point so the guard knows
which record (and therefore which real PII / consent) a call relates to.

Why monkeypatch: ``LLMApiService`` is a plain Python class instantiated directly
at its call sites — it is NOT an ``models.Model``, so there is no ``_inherit`` /
registry hook. Patching the methods is the only seam. The mandatory signature
smoke test (tests/test_native_ai_signatures.py) fails loudly if Odoo changes any
patched signature on upgrade, so the guard can never be silently disabled.

Four patch points (all idempotent):
  1. LLMApiService.request_llm        — text chat (the main funnel)
  2. LLMApiService.get_embedding      — embedding input text
  3. LLMApiService.get_transcription  — audio (cannot redact; consent-gated)
  4. ir.actions.server._ai_action_run — CONTEXT CAPTURE: stamps the in-context
                                        record so 1–3 can resolve real PII

Per-call pipeline (pre-flight, BEFORE any outbound HTTP):
  consent → PII redaction (record-driven + regex net) → cost cap → audit →
  delegate to the original → re-insert real values → record usage.

Guarantees enforced: PDPL (consent + record-driven redaction), hard cost cap
(Rule 14), persistent audit (Rule 20), env-only secrets (Rule 03). The in-memory
PII map lives only for the duration of one call and is never persisted or sent.
"""
import inspect
import logging

from odoo.exceptions import UserError

from .pii_redaction import Redactor, RedactionError

_logger = logging.getLogger(__name__)

_SENTINEL = "_era_crm_ai_guard_installed"

# Signatures of the NATIVE methods captured at patch time (before wrapping), so
# the mandatory smoke test can detect an Odoo upgrade that changes a patched
# signature and fail loudly — the guard must never be silently disabled.
ORIGINAL_SIGNATURES = {}

# Context keys the guard reads.
CTX_RECORD = "_pdpl_record"            # recordset stamped by patch #4 / our mixin
CTX_AGENT = "_crm_ai_agent_tech_name"  # str, for cost attribution (optional)
CTX_UNATTENDED = "crm_ai_unattended"   # True for cron/batch: skip-with-audit, no raise

# Native UI key parameters that MUST stay blank (Rule 03 — keys only from env).
_NATIVE_UI_KEY_PARAM = {
    "openai": "ai.openai_key",
    "google": "ai.google_key",
}


# ======================================================================
# Install
# ======================================================================
def install():
    """Idempotently monkeypatch the native AI egress + record-capture seams.

    Called once at module import (services/__init__.py). Safe to call again on a
    registry reload — the sentinel prevents double-wrapping.
    """
    try:
        from odoo.addons.ai.utils import llm_api_service as _svc
        from odoo.addons.ai.models import ir_actions_server as _ias
    except ImportError:
        # The native 'ai' module is a hard dependency (manifest). If it is truly
        # absent the guard cannot protect anything — fail loudly rather than run
        # AI unguarded.
        _logger.exception("era_crm_ai_agents_base: native 'ai' module not "
                          "importable; the AI Compliance Guard is NOT active.")
        raise

    Service = _svc.LLMApiService
    Actions = _ias.IrActionsServer

    if getattr(Service, _SENTINEL, False):
        return  # already installed

    _orig_request_llm = Service.request_llm
    _orig_get_embedding = Service.get_embedding
    _orig_get_transcription = Service.get_transcription
    _orig_ai_action_run = Actions._ai_action_run

    # Snapshot native signatures BEFORE wrapping (drift detection in tests).
    ORIGINAL_SIGNATURES.update({
        "request_llm": list(inspect.signature(_orig_request_llm).parameters),
        "get_embedding": list(inspect.signature(_orig_get_embedding).parameters),
        "get_transcription": list(inspect.signature(_orig_get_transcription).parameters),
        "_ai_action_run": list(inspect.signature(_orig_ai_action_run).parameters),
    })

    # ---- 1. text chat -------------------------------------------------
    def request_llm(self, llm_model, system_prompts, user_prompts, tools=None,
                    files=None, schema=None, temperature=0.2, inputs=None,
                    web_grounding=False):
        def _call(masked_system, masked_user):
            return _orig_request_llm(
                self, llm_model, masked_system, masked_user, tools=tools,
                files=files, schema=schema, temperature=temperature,
                inputs=inputs, web_grounding=web_grounding,
            )
        return _guard_text(self, _call, llm_model, system_prompts, user_prompts)

    # ---- 2. embeddings ------------------------------------------------
    def get_embedding(self, input, dimensions, model="text-embedding-3-small",
                      encoding_format=None, user=None):
        def _call(masked_input):
            return _orig_get_embedding(
                self, masked_input, dimensions, model=model,
                encoding_format=encoding_format, user=user,
            )
        return _guard_embedding(self, _call, input)

    # ---- 3. transcription (audio: consent-gated, no text to redact) ----
    def get_transcription(self, data, mimetype="audio/ogg", model="whisper-1",
                          language=None, prompt=None,
                          response_format="verbose_json", temperature=None):
        def _call():
            return _orig_get_transcription(
                self, data, mimetype=mimetype, model=model, language=language,
                prompt=prompt, response_format=response_format,
                temperature=temperature,
            )
        return _guard_transcription(self, _call)

    # ---- 4. context capture (record-aware entry point) ----------------
    def _ai_action_run(self, record):
        return _orig_ai_action_run(self.with_context(**{CTX_RECORD: record}), record)

    Service.request_llm = request_llm
    Service.get_embedding = get_embedding
    Service.get_transcription = get_transcription
    Actions._ai_action_run = _ai_action_run
    setattr(Service, _SENTINEL, True)
    _logger.info("era_crm_ai_agents_base: AI Compliance Guard installed "
                 "(request_llm, get_embedding, get_transcription, _ai_action_run).")


# ======================================================================
# Pipelines
# ======================================================================
def _guard_text(service, call, llm_model, system_prompts, user_prompts):
    """Full pipeline for a text chat call. ``call(masked_system, masked_user)``
    invokes the original with redacted prompts and returns ``list[str]``."""
    env = service.env
    _assert_env_only_key(service)                       # Rule 03
    record = _resolve_record(env)
    agent = _resolve_agent(env)

    capped = _enforce_cost_cap(env, agent, record)      # Rule 14 (pre-spend)
    if capped is not None:
        return capped

    redactor = _build_redactor(record)
    # Consent gate (PDPL): if real PII is present, every involved partner must
    # have consented to international processing — else block (never send).
    if redactor.has_values() and not _all_consented(_consent_partners(record) or []):
        return _block(env, record, "blocked_no_consent", {}, text=True)

    sys_in = list(system_prompts or [])
    usr_in = list(user_prompts or [])
    masked_system, leftover_s = _mask_all(redactor, sys_in)
    masked_user, leftover_u = _mask_all(redactor, usr_in)
    leftover = leftover_s + leftover_u
    if leftover:                                        # fail-safe: unmapped PII
        return _block(env, record, "blocked_unmapped_pii",
                      {"patterns": [k for k, _ in leftover]}, text=True)

    _audit(env, "ai_request", agent, record,
           {"model": llm_model},
           {"decision": "allowed", "pii_tokens": len(redactor.mapping)})

    responses = call(masked_system, masked_user)       # outbound (PII masked)

    try:
        restored = [redactor.unmask(r) for r in (responses or [])]
    except RedactionError as exc:
        return _block(env, record, "redaction_reinsert_failed",
                      {"error": str(exc)}, text=True)

    _record_usage(env, agent, llm_model, sys_in + usr_in, restored)  # Rule 14 feed
    return restored


def _guard_embedding(service, call, input):
    """Embedding input is text that would leave the Kingdom — redact it too."""
    env = service.env
    _assert_env_only_key(service)
    record = _resolve_record(env)
    agent = _resolve_agent(env)
    redactor = _build_redactor(record)
    if redactor.has_values() and not _all_consented(_consent_partners(record) or []):
        return _block(env, record, "blocked_no_consent", {"op": "embedding"},
                      text=False)

    items = input if isinstance(input, list) else [input]
    masked = []
    leftover_all = []
    for item in items:
        if isinstance(item, str):
            m, leftover = redactor.mask(item)
            masked.append(m)
            leftover_all += leftover
        else:
            masked.append(item)  # token-id inputs: nothing to redact
    if leftover_all:
        return _block(env, record, "blocked_unmapped_pii",
                      {"patterns": [k for k, _ in leftover_all]}, text=False)
    _audit(env, "ai_request", agent, record, {"op": "embedding"},
           {"decision": "allowed", "pii_tokens": len(redactor.mapping)})
    return call(masked if isinstance(input, list) else masked[0])


def _guard_transcription(service, call):
    """Audio cannot be text-redacted; gate strictly on consent before sending."""
    env = service.env
    _assert_env_only_key(service)
    record = _resolve_record(env)
    agent = _resolve_agent(env)
    # No record context -> we cannot establish consent for the audio -> block.
    partners = _consent_partners(record)
    if not partners or not _all_consented(partners):
        return _block(env, record, "blocked_no_consent",
                      {"op": "transcription"}, text=False)
    _audit(env, "ai_request", agent, record, {"op": "transcription"},
           {"decision": "allowed"})
    return call()


# ======================================================================
# Pipeline steps / helpers
# ======================================================================
def _assert_env_only_key(service):
    """Rule 03: refuse to run if a key was pasted into the native AI UI (DB).

    Keys must come only from the environment; the native UI key fields must be
    left blank. A DB-stored key is a misconfiguration we fail closed on.
    """
    param = _NATIVE_UI_KEY_PARAM.get(getattr(service, "provider", None))
    if not param:
        return
    if service.env["ir.config_parameter"].sudo().get_param(param):
        raise UserError(
            "PDPL/Rule 03: an API key is set in the Odoo AI settings (%s). "
            "Keys must come only from the server environment; clear the AI "
            "key field in Settings." % param
        )


def _resolve_record(env):
    """The record a call relates to: explicit stamp first, then active_model/id."""
    rec = env.context.get(CTX_RECORD)
    if rec is not None and getattr(rec, "_name", False):
        return rec[:1] if len(rec) > 1 else rec
    model = env.context.get("active_model")
    res_id = env.context.get("active_id")
    if model and res_id and model in env:
        try:
            return env[model].browse(res_id)
        except Exception:  # pragma: no cover - defensive
            return None
    return None


def _resolve_agent(env):
    """Optional crm.ai.agent for cost attribution; None for Odoo's own AI."""
    tech_name = env.context.get(CTX_AGENT)
    if not tech_name or "crm.ai.agent" not in env:
        return None
    return env["crm.ai.agent"].sudo().with_context(active_test=False).search(
        [("tech_name", "=", tech_name)], limit=1
    ) or None


def _enforce_cost_cap(env, agent, record):
    """Rule 14: block before any spend when the agent is over its monthly cap.

    Returns None when the call may proceed, or the block result (interactive:
    raises; unattended: empty result) which the caller must return as-is.
    """
    if not agent or "crm.ai.usage" not in env:
        return None
    if agent.state in ("paused", "capped") or env["crm.ai.usage"].is_over_cap(agent):
        if agent.state == "enabled":
            agent._mark_state("capped")
        return _block(env, record, "cost_cap_exceeded",
                      {"monthly_cost": agent.monthly_cost}, text=True, event="blocked")
    return None


# ---- redaction wiring -------------------------------------------------
def _build_redactor(record):
    """Build a Redactor from the real personal values on the record + its partner.

    Consent is enforced separately by the calling pipeline: if this redactor has
    values (real PII is present) the call is blocked unless every involved
    partner has consented (see ``_all_consented`` / ``_consent_partners``).
    """
    redactor = Redactor()
    if record is None:
        return redactor
    _harvest_pii(record, redactor)
    return redactor


def _harvest_pii(record, redactor):
    """Pull name/phone/mobile/email/national-id from a record and its partner."""
    if not getattr(record, "_name", False):
        return
    record = record[:1]
    if not record.id:
        return
    fields = record._fields
    # res.partner-style fields on the record itself.
    if "name" in fields and record._name in ("res.partner",):
        redactor.add("PERSON", record.name, split_parts=True)
    for fname, kind in (("phone", "PHONE"), ("mobile", "PHONE"),
                        ("email", "EMAIL"), ("vat", "NATIONAL_ID")):
        if fname in fields and record[fname]:
            redactor.add(kind, record[fname])
    # crm.lead-style raw contact fields.
    for fname, kind, split in (("contact_name", "PERSON", True),
                               ("email_from", "EMAIL", False),
                               ("phone", "PHONE", False),
                               ("mobile", "PHONE", False)):
        if fname in fields and record[fname]:
            redactor.add(kind, record[fname], split_parts=split)
    # Linked partner.
    if "partner_id" in fields and record.partner_id:
        p = record.partner_id
        redactor.add("PERSON", p.name, split_parts=True)
        for fname, kind in (("phone", "PHONE"), ("mobile", "PHONE"),
                            ("email", "EMAIL"), ("vat", "NATIONAL_ID")):
            if fname in p._fields and p[fname]:
                redactor.add(kind, p[fname])


def _mask_all(redactor, texts):
    """Mask a list of prompt strings; return (masked_list, leftover_pii)."""
    out, leftover = [], []
    for t in texts:
        m, lo = redactor.mask(t)
        out.append(m)
        leftover += lo
    return out, leftover


# ---- consent ----------------------------------------------------------
def _consent_partners(record):
    """Return the res.partner recordset whose consent governs this call."""
    if record is None or not getattr(record, "_name", False):
        return None
    record = record[:1]
    if not record.id:
        return None
    if record._name == "res.partner":
        return record
    if "partner_id" in record._fields and record.partner_id:
        return record.partner_id
    return None


def _all_consented(partners):
    """True only if there is at least one partner and ALL of them consented.

    Fail-safe: no partner to read consent from (empty/None) counts as NOT
    consented, so PII with no consent source is blocked rather than sent.
    """
    if not partners:
        return False
    field = "crm_ai_intl_processing_consent"
    for p in partners:
        if field not in p._fields or not p[field]:
            return False
    return True


# ---- audit / blocking / usage ----------------------------------------
def _audit(env, decision_kind, agent, record, before, after, event="ai_request"):
    """Append one audit row (Rule 20). Never carries raw PII — only counts."""
    if "crm.ai.audit.log" not in env:
        return
    try:
        env["crm.ai.audit.log"].log(event, agent or None, record, before, after)
    except Exception:  # pragma: no cover - logging must never break the call path
        _logger.exception("AI guard: failed to write audit entry (%s).", decision_kind)


def _block(env, record, decision, detail, text=True, event="blocked"):
    """Refuse the outbound call. PII never left (we block pre-delegate).

    Unattended (cron/batch): audit decision=<decision>_skipped and return an
    empty result so the batch drops this customer and continues — NEVER sends.
    Interactive: audit and raise UserError.
    """
    unattended = bool(env.context.get(CTX_UNATTENDED))
    reason = decision + ("_skipped" if unattended else "")
    _audit(env, decision, _resolve_agent(env), record,
           detail, {"decision": reason}, event=event)
    if unattended:
        _logger.info("AI guard: %s (unattended) — call dropped, not sent.", reason)
        return [] if text else None
    raise UserError(_block_message(decision))


def _block_message(decision):
    return {
        "blocked_no_consent": (
            "PDPL: this customer has not consented to processing by an "
            "international AI provider. The request was not sent."),
        "blocked_unmapped_pii": (
            "PDPL: the request contains personal data that could not be safely "
            "masked. The request was not sent."),
        "redaction_reinsert_failed": (
            "PDPL: the AI response could not be safely restored and was withheld."),
        "cost_cap_exceeded": (
            "This AI agent has hit its monthly cost cap and has been paused "
            "(Rule 14)."),
    }.get(decision, "The AI request was blocked by the compliance guard.")


def _record_usage(env, agent, llm_model, in_texts, out_texts):
    """Estimate tokens (native does not return counts here) and record usage.

    Token estimate matches Odoo's own heuristic (~1 token / 4 chars). Cost is
    priced from the crm.ai.model catalog by model code. Skipped when no agent is
    in context (e.g. Odoo's own AI features) — those have no cost bucket here.
    """
    if not agent or "crm.ai.usage" not in env:
        return
    in_tok = sum(len(t) for t in in_texts if isinstance(t, str)) // 4
    out_tok = sum(len(t) for t in out_texts if isinstance(t, str)) // 4
    model = False
    if "crm.ai.model" in env and llm_model:
        model = env["crm.ai.model"].sudo().search([("code", "=", llm_model)], limit=1)
    cost = 0.0
    if model:
        cost = (in_tok / 1000.0) * (model.price_input_1k or 0.0) \
            + (out_tok / 1000.0) * (model.price_output_1k or 0.0)
    try:
        env["crm.ai.usage"].record(agent, model or False, in_tok, out_tok, cost)
    except Exception:  # pragma: no cover
        _logger.exception("AI guard: failed to record usage.")

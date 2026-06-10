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

Guarantees enforced: PDPL (consent + record-driven redaction), hard consumption
limit (Rule 14: dollar cost cap on the priced API path, estimated-token limit on
the Claude CLI/subscription path), persistent audit (Rule 20), env-only secrets
(Rule 03; the CLI path uses no API key, so there is no secret to leak). The
in-memory PII map lives only for one call and is never persisted or sent.
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

# Provider token of the era_ai_accounts Claude CLI / subscription transport. A
# call on this provider has no per-token dollar cost, so on our agents' path it is
# governed by the monthly TOKEN limit (estimated) instead of the dollar cost cap,
# and is exempt from the unpriced-model rate-card block. The redaction/consent
# guarantees are identical — the guard still sits OUTERMOST on the public
# request_llm, so prompts are masked BEFORE the CLI subprocess ever runs.
CLI_PROVIDER = "anthropic_cli"

# Native UI key parameters that MUST stay blank (Rule 03 — keys only from env).
_NATIVE_UI_KEY_PARAM = {
    "openai": "ai.openai_key",
    "google": "ai.google_key",
}

# Configurable protection layers (Change 1). ir.config_parameter, all default ON.
#   Operational (toggle freely): enable_cost_cap, enable_audit.
#   Compliance / PDPL (friction): enable_pii_redaction, enable_consent_check —
#     changed only via the manager-only Settings field, audited on True->False,
#     and warning-logged on EVERY call while off (see _compliance_on).
# The env-only-key assertion (Rule 03) is NOT a toggle; it is always enforced.
_FLAG_PREFIX = "era_crm_ai_agents."


def _flag(env, name, default=True):
    """Read a boolean protection toggle from ir.config_parameter (default ON)."""
    raw = env["ir.config_parameter"].sudo().get_param(_FLAG_PREFIX + name)
    if raw in (None, ""):
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _compliance_on(env, name):
    """Read a COMPLIANCE toggle and warn LOUDLY on every call while it is off.

    PDPL protection must never be disabled *silently*: even if the underlying
    config param was flipped outside the manager Settings UI, every call made
    while a compliance layer is off emits this warning.
    """
    on = _flag(env, name)
    if not on:
        _logger.warning(
            "PDPL: %s%s is DISABLED — data is being sent to an international AI "
            "provider WITHOUT this protection for this call.", _FLAG_PREFIX, name)
    return on


def _model_is_priced(env, llm_model):
    """True if the rate card (crm.ai.model) has an active price for this code."""
    if "crm.ai.model" not in env or not llm_model:
        return False
    return bool(env["crm.ai.model"].sudo().search_count(
        [("code", "=", llm_model), ("active", "=", True)]))


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
def _is_our_agent_call(env):
    """The security-relevant SCOPE GATE (Option C — hybrid enforcement).

    Returns True when the call originates from one of OUR agents, signalled by
    the CTX_AGENT context key that crm.ai.agent.mixin._call_llm stamps.

      * OUR agents  (CTX_AGENT set)  -> FULL enforcement: cost cap + consent gate
        + record-driven redaction + regex fail-safe HARD-BLOCK + audit + usage.
        This is the path that carries REAL customer data, so it must never let an
        unmapped PII value through.
      * native AI   (no CTX_AGENT)   -> best-effort record-PII redaction + audit,
        but NO hard block, so Odoo's own Ask-AI / ai_fields keep working.

    ACCEPTED TRADE-OFF: on the native path a PII value the record-driven redactor
    does not know about CAN egress (there is no regex hard-block). That is why
    full hard enforcement MUST remain tied to CTX_AGENT — our agents' path.

    The env-only-key assertion (Rule 03) is enforced GLOBALLY, before this gate,
    on every call regardless of origin.
    """
    return bool(env.context.get(CTX_AGENT))


def _guard_text(service, call, llm_model, system_prompts, user_prompts):
    """Text chat entry: global key check, then branch on the scope gate."""
    env = service.env
    _assert_env_only_key(service)                       # GLOBAL (Rule 03)
    is_cli = getattr(service, "provider", None) == CLI_PROVIDER
    record = _resolve_record(env)
    agent = _resolve_agent(env)
    redactor = _build_redactor(record)
    sys_in = list(system_prompts or [])
    usr_in = list(user_prompts or [])
    if is_cli and not _is_our_agent_call(env):
        # TRIPWIRE. A Claude-CLI call reached the guard WITHOUT CTX_AGENT, so it
        # will fall to the best-effort native path (NO consent gate, NO unmapped-
        # PII hard-block). Our agents MUST reach the CLI through
        # crm.ai.agent.mixin._call_llm, which stamps CTX_AGENT. A hit here means
        # an ERA agent bypassed the mixin — surface it loudly.
        _logger.warning(
            "TRIPWIRE: a Claude-CLI LLM call arrived WITHOUT CTX_AGENT — it will "
            "use best-effort redaction only (PDPL consent + unmapped-PII hard-"
            "block NOT enforced). ERA agents must call the CLI via "
            "crm.ai.agent.mixin._call_llm.")
    if _is_our_agent_call(env):
        return _text_strict(env, call, llm_model, record, agent, redactor,
                            sys_in, usr_in, is_cli)
    return _text_native(env, call, llm_model, record, agent, redactor, sys_in, usr_in)


def _text_strict(env, call, llm_model, record, agent, redactor, sys_in, usr_in,
                 is_cli=False):
    """OUR agents — full enforcement, each layer behind its toggle (all default
    ON). Compliance layers warn-on-every-call when disabled; operational layers
    toggle silently. ``is_cli`` selects the consumption control: estimated-token
    limit (CLI/subscription) vs dollar cost cap (priced API)."""
    cap_on = _flag(env, "enable_cost_cap")              # operational
    audit_on = _flag(env, "enable_audit")               # operational
    redaction_on = _compliance_on(env, "enable_pii_redaction")
    consent_on = _compliance_on(env, "enable_consent_check")

    if cap_on:                                          # Rule 14 (pre-spend)
        capped = _enforce_consumption(env, agent, record, is_cli)
        if capped is not None:
            return capped

    # Consent gate (PDPL): real PII present -> every partner must have consented.
    if consent_on and redactor.has_values() and \
            not _all_consented(_consent_partners(record) or []):
        return _block(env, record, "blocked_no_consent", {}, text=True)

    # Record-driven redaction (PDPL). When off, prompts go out unmasked (warned).
    if redaction_on:
        masked_system, leftover_s = _mask_all(redactor, sys_in)
        masked_user, leftover_u = _mask_all(redactor, usr_in)
        leftover = leftover_s + leftover_u
        if leftover:                                    # fail-safe: unmapped PII
            return _block(env, record, "blocked_unmapped_pii",
                          {"patterns": [k for k, _ in leftover]}, text=True)
    else:
        masked_system, masked_user = sys_in, usr_in

    # Unpriced-model fail-safe (Rule 14) — DOLLAR path only. A priced-path model
    # with no rate-card price must NOT silently cost 0. The CLI/subscription path
    # has no per-token dollar cost at all (cost is always 0) and is governed by
    # the token limit above, not the rate card — so the unpriced block is
    # intentionally skipped for it (an opus/sonnet alias is never "priced").
    unpriced = False
    if cap_on and agent and not is_cli and not _model_is_priced(env, llm_model):
        unpriced = True
        _logger.warning("Rule 14: model %r has no active price in the rate card "
                        "(crm.ai.model); cost cannot be computed.", llm_model)
        if _flag(env, "block_unpriced_model", default=True):
            return _block(env, record, "unpriced_model",
                          {"model": llm_model}, text=True, event="unpriced_model")
        _audit(env, "unpriced_model", agent, record,
               {"model": llm_model}, {"decision": "allowed_unpriced"},
               event="unpriced_model")

    if audit_on:
        _audit(env, "ai_request", agent, record,
               {"model": llm_model, "scope": "agent",
                "transport": "cli" if is_cli else "api",
                "redaction": redaction_on, "consent": consent_on},
               {"decision": "allowed", "pii_tokens": len(redactor.mapping)})

    responses = call(masked_system, masked_user)       # outbound (PII masked)

    if redaction_on:
        try:
            restored = [redactor.unmask(r) for r in (responses or [])]   # strict
        except RedactionError as exc:
            return _block(env, record, "redaction_reinsert_failed",
                          {"error": str(exc)}, text=True)
    else:
        restored = responses or []

    if cap_on:
        _record_usage(env, agent, llm_model, sys_in + usr_in, restored,
                      unpriced=unpriced, is_cli=is_cli)  # Rule 14 feed
    return restored


def _text_native(env, call, llm_model, record, agent, redactor, sys_in, usr_in):
    """Native Ask-AI / ai_fields — best-effort record-PII masking + audit, NO
    hard block, so native features keep working. PII the redactor does not know
    about may egress here (accepted trade-off; see _is_our_agent_call)."""
    redaction_on = _compliance_on(env, "enable_pii_redaction")
    if redaction_on:
        masked_system, leftover_s = _mask_all(redactor, sys_in)
        masked_user, leftover_u = _mask_all(redactor, usr_in)
        unmapped = len(leftover_s) + len(leftover_u)
    else:
        masked_system, masked_user = sys_in, usr_in
        unmapped = 0
    if _flag(env, "enable_audit"):
        _audit(env, "ai_request", agent, record,
               {"model": llm_model, "scope": "native"},
               {"decision": "allowed_native",
                "pii_tokens": len(redactor.mapping),
                "unmapped_pii_seen": unmapped})
    responses = call(masked_system, masked_user)
    if not redaction_on:
        return responses or []
    # Best-effort restore; never withhold native output, never raise.
    return [redactor.unmask(r, strict=False) for r in (responses or [])]


def _guard_embedding(service, call, input):
    """Embedding input is text that would leave the Kingdom — redact it too.
    Hard consent/PII block only for OUR agents; native gets best-effort masking."""
    env = service.env
    _assert_env_only_key(service)                       # GLOBAL (Rule 03)
    record = _resolve_record(env)
    agent = _resolve_agent(env)
    redactor = _build_redactor(record)
    our = _is_our_agent_call(env)
    redaction_on = _compliance_on(env, "enable_pii_redaction")
    consent_on = _compliance_on(env, "enable_consent_check")
    if our and consent_on and redactor.has_values() and \
            not _all_consented(_consent_partners(record) or []):
        return _block(env, record, "blocked_no_consent", {"op": "embedding"},
                      text=False)
    items = input if isinstance(input, list) else [input]
    masked = []
    leftover_all = []
    for item in items:
        if isinstance(item, str) and redaction_on:
            m, leftover = redactor.mask(item)
            masked.append(m)
            leftover_all += leftover
        else:
            masked.append(item)  # token-id inputs / redaction-off: leave as-is
    if our and redaction_on and leftover_all:           # hard-block: our agents only
        return _block(env, record, "blocked_unmapped_pii",
                      {"patterns": [k for k, _ in leftover_all]}, text=False)
    if _flag(env, "enable_audit"):
        _audit(env, "ai_request", agent, record,
               {"op": "embedding", "scope": "agent" if our else "native"},
               {"decision": "allowed" if our else "allowed_native",
                "pii_tokens": len(redactor.mapping),
                "unmapped_pii_seen": 0 if our else len(leftover_all)})
    return call(masked if isinstance(input, list) else masked[0])


def _guard_transcription(service, call):
    """Audio cannot be text-redacted. OUR agents: gate strictly on consent.
    Native (e.g. voip_ai): audit only, never block a native feature."""
    env = service.env
    _assert_env_only_key(service)                       # GLOBAL (Rule 03)
    record = _resolve_record(env)
    agent = _resolve_agent(env)
    audit_on = _flag(env, "enable_audit")
    if _is_our_agent_call(env):
        if _compliance_on(env, "enable_consent_check"):
            partners = _consent_partners(record)
            if not partners or not _all_consented(partners):
                return _block(env, record, "blocked_no_consent",
                              {"op": "transcription"}, text=False)
        if audit_on:
            _audit(env, "ai_request", agent, record,
                   {"op": "transcription", "scope": "agent"}, {"decision": "allowed"})
        return call()
    if audit_on:
        _audit(env, "ai_request", agent, record,
               {"op": "transcription", "scope": "native"}, {"decision": "allowed_native"})
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


def _enforce_consumption(env, agent, record, is_cli):
    """Rule 14: block before any spend when the agent is over its monthly limit.

    Path-aware, same shape/outcome (block + auto-pause to ``capped`` + audit):
      * CLI/subscription -> estimated-TOKEN limit (``is_over_token_limit``).
      * priced API       -> dollar cost cap     (``is_over_cap``).

    Returns None when the call may proceed, or the block result (interactive:
    raises; unattended: empty result) which the caller must return as-is.
    """
    if not agent or "crm.ai.usage" not in env:
        return None
    Usage = env["crm.ai.usage"]
    over = Usage.is_over_token_limit(agent) if is_cli else Usage.is_over_cap(agent)
    if agent.state in ("paused", "capped") or over:
        if agent.state == "enabled":
            agent._mark_state("capped")
        if is_cli:
            return _block(env, record, "token_limit_exceeded",
                          {"monthly_tokens": agent.monthly_tokens},
                          text=True, event="blocked")
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
        "token_limit_exceeded": (
            "This AI agent has reached its monthly token limit on the Claude "
            "subscription (CLI) path and has been paused (Rule 14). The token "
            "count is estimated. Raise the limit under CRM AI → "
            "Configuration, or wait for next month."),
        "unpriced_model": (
            "This AI model has no price in the rate card, so its cost cannot be "
            "tracked against the cap. The request was blocked (Rule 14). Add a "
            "price for it under CRM AI → Configuration → Models."),
    }.get(decision, "The AI request was blocked by the compliance guard.")


def _record_usage(env, agent, llm_model, in_texts, out_texts, unpriced=False,
                  is_cli=False):
    """Estimate tokens and record usage.

    Token counts are ESTIMATED (~1 token / 4 chars): the native public
    request_llm returns only text, not provider usage, so no measured count is
    available at this seam — the usage row is marked ``token_source=estimated``
    accordingly (CLI and API alike). Cost:

      * CLI/subscription (``is_cli``) -> 0 by design; consumption is the token
        count, governed by the token limit. Row flagged ``via_cli``.
      * priced API -> priced from the rate card (crm.ai.model) by model code.
      * unpriced API -> 0, flagged ``unpriced`` so a manager can add a price.

    Skipped when no agent is in context (e.g. Odoo's own AI features).
    """
    if not agent or "crm.ai.usage" not in env:
        return
    in_tok = sum(len(t) for t in in_texts if isinstance(t, str)) // 4
    out_tok = sum(len(t) for t in out_texts if isinstance(t, str)) // 4
    model = False
    if "crm.ai.model" in env and llm_model and not is_cli:
        model = env["crm.ai.model"].sudo().search([("code", "=", llm_model)], limit=1)
    cost = 0.0
    if model and not unpriced and not is_cli:
        cost = (in_tok / 1000.0) * (model.price_input_1k or 0.0) \
            + (out_tok / 1000.0) * (model.price_output_1k or 0.0)
    try:
        env["crm.ai.usage"].record(agent, model or False, in_tok, out_tok, cost,
                                   unpriced=unpriced, token_source="estimated",
                                   via_cli=is_cli)
    except Exception:  # pragma: no cover
        _logger.exception("AI guard: failed to record usage.")

# -*- coding: utf-8 -*-
"""Dead-Lead message-drafting engine (template-bound; AI fills only slots).

    draft_message(lead, trigger) -> text   (high sensitivity)

SAFETY MODEL — why the body can never go off-script (this task's acceptance):

* The message body is a MANAGER-APPROVED template (crm.ai.dead.lead.template).
  The template text outside ``{{slots}}`` is NEVER sent to the LLM and is NEVER
  rewritten — we only substitute slot VALUES into it (a single regex pass over
  the original body), so the body stays template-bound by construction.
* DATA slots (``name`` / ``company`` / ``salesperson``) are filled
  deterministically from CRM records — no AI, and minimal PII leaves our system.
* The ONLY AI-authored part is the ``personal_note`` slot: one short, warm
  Arabic re-opening sentence. It is produced via the agent's router
  (``_call_llm``, high sensitivity) behind the Base Compliance Guard (consent +
  PII redaction + cap + audit), then sanitised (any injected ``{{}}`` stripped,
  length-clamped, collapsed to a single value) so it cannot inject new slots or
  overflow the template.
* Every draft still passes the human approval gate (task 3.5) before any send.

Runs under the calling salesperson (NO sudo) — Rule 09.
"""
import logging
import re

_logger = logging.getLogger(__name__)

# Slot syntax: {{slot}} (double-brace, distinct from the guard's [[PII:..]] token).
PLACEHOLDER_RE = re.compile(r"\{\{\s*(\w+)\s*\}\}")

# Slots filled deterministically from records (never sent to / authored by the
# LLM — data minimisation):
DATA_SLOTS = frozenset({"name", "company", "salesperson"})
# Slots the LLM authors — the ONLY AI-filled, genuinely personal parts:
AI_SLOTS = frozenset({"personal_note"})

# Technical safety clamp on AI slot output (anti-runaway / anti-injection); a
# genuine internal guard, not a business policy value.
MAX_AI_SLOT_CHARS = 300
_AI_MAX_OUTPUT_TOKENS = 256


class DraftEngine:
    """Assemble a template-bound comeback message for a (lead, trigger).

    ``agent`` is a ``crm.ai.dead.lead.agent`` record (carries env, the approved
    ``template_id``, and the router via the mixin). ``unattended`` is threaded to
    ``_call_llm`` so a guard block in a cron run is dropped-with-audit (empty
    note) instead of raising.
    """

    def __init__(self, agent, unattended=False):
        agent.ensure_one()
        self.agent = agent
        self.env = agent.env
        self.unattended = unattended

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def draft_message(self, lead, trigger):
        template = self.agent.template_id
        if not template or not template.body:
            _logger.info(
                "Dead-Lead: no approved template configured; nothing to draft "
                "for lead %s.", lead.id)
            return ""

        body = template.body
        slots = set(PLACEHOLDER_RE.findall(body))

        values = {}
        values.update(self._data_slot_values(lead, slots))
        if slots & AI_SLOTS:
            values.update(self._ai_slot_values(lead, trigger, slots & AI_SLOTS))

        return self._render(body, values)

    # ------------------------------------------------------------------
    # Data slots — deterministic, no AI, minimal PII
    # ------------------------------------------------------------------
    def _data_slot_values(self, lead, slots):
        out = {}
        partner = lead.partner_id
        if "name" in slots:
            out["name"] = (
                lead.contact_name or (partner.name if partner else "")
                or lead.partner_name or "")
        if "company" in slots:
            out["company"] = (
                lead.partner_name
                or (partner.commercial_company_name if partner else "") or "")
        if "salesperson" in slots:
            out["salesperson"] = lead.user_id.name or ""
        return out

    # ------------------------------------------------------------------
    # AI slot — the only LLM-authored part (template-bound, sanitised)
    # ------------------------------------------------------------------
    def _ai_slot_values(self, lead, trigger, ai_slots):
        trigger = trigger or {}
        bucket = trigger.get("reason_bucket", "other")
        days = trigger.get("days_elapsed")

        system = (
            "You write a single short re-engagement sentence in Modern Standard "
            "Arabic for a Saudi B2B salesperson reconnecting with a previously "
            "lost lead. Output ONLY that one sentence — no greeting, no name, no "
            "signature, no quotes, no preamble, no markdown, no placeholders. "
            "Warm, respectful, professional KSA business tone. Make NO new "
            "offers, prices, claims, links, or promises; do not invent facts."
        )
        # Context carries NO direct PII (the name/company are filled by us, not
        # the model). record=lead still drives the guard's consent + redaction.
        ctx_lines = ["Reason the lead was lost (category): %s" % bucket]
        if days is not None:
            ctx_lines.append("Approx. days since the lead went cold: %s" % days)
        prompt = (
            "Write the re-opening sentence now.\n" + "\n".join(ctx_lines)
        )

        try:
            texts = self.agent._call_llm(
                prompt, sensitivity="high", system=system, record=lead,
                unattended=self.unattended, max_output_tokens=_AI_MAX_OUTPUT_TOKENS)
        except Exception:
            if not self.unattended:
                raise
            _logger.warning(
                "Dead-Lead: AI slot fill failed for lead %s; leaving note empty.",
                lead.id)
            texts = []

        note = self._sanitize_ai(texts)
        # All AI slots share the single authored sentence (today only
        # personal_note exists; this stays correct if more are added).
        return {slot: note for slot in ai_slots}

    @staticmethod
    def _sanitize_ai(texts):
        if not texts:
            return ""
        raw = texts[0] if isinstance(texts, (list, tuple)) else texts
        if not raw:
            return ""
        # Strip any placeholder tokens the model may have echoed (anti-injection),
        # collapse whitespace/newlines to single spaces, clamp length.
        cleaned = PLACEHOLDER_RE.sub("", raw)
        cleaned = " ".join(cleaned.split()).strip().strip('"').strip()
        if len(cleaned) > MAX_AI_SLOT_CHARS:
            cleaned = cleaned[:MAX_AI_SLOT_CHARS].rstrip()
        return cleaned

    # ------------------------------------------------------------------
    # Render — single pass over the ORIGINAL body; unknown slots -> empty
    # ------------------------------------------------------------------
    def _render(self, body, values):
        def repl(match):
            return values.get(match.group(1), "")

        text = PLACEHOLDER_RE.sub(repl, body)
        # Tidy blank lines left by an empty optional slot (no content change).
        text = re.sub(r"[ \t]+\n", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

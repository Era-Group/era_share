# -*- coding: utf-8 -*-
"""Dead-Lead classification + trigger detection (read-only, no sudo).

    classify_lost(lead)  -> reason_bucket (str)
    detect_trigger(lead) -> trigger (dict) | None

A *trigger* is the reason the agent believes a previously closed-lost lead is
worth re-approaching NOW. Detection is deliberately CONSERVATIVE: detect_trigger
returns None unless the lead passes every qualifying gate, so the scan (3.7)
only ever drafts for genuinely qualifying leads (this task's acceptance).

Stateless and side-effect-free: it only READS the lead, its lost reason, and the
agent's manager-configured threshold. It runs under the calling salesperson
(NO sudo) — Rule 09. Cooldown / de-duplication (don't re-contact the same lead
on every run) is the scan's responsibility (3.7).
"""
import logging

from odoo import fields

_logger = logging.getLogger(__name__)

# Trigger types. Only ELAPSED_TIME is implemented in module 3 — it needs no
# signal beyond the lead itself. CHAMPION_MOVED and PRICE_DROP are declared so
# the drafting engine (3.4) has a stable vocabulary, but their DETECTION is
# deferred: they depend on signals this module does not own yet (a contact-change
# feed / a product-price feed). They are documented hooks that currently yield
# no trigger — see _detect_champion_moved / _detect_price_drop below.
TRIGGER_ELAPSED_TIME = "elapsed_time"
TRIGGER_CHAMPION_MOVED = "champion_moved"
TRIGGER_PRICE_DROP = "price_drop"

DEFAULT_BUCKET = "other"


class TriggerDetector:
    """Classify a lost lead and decide whether a fresh trigger fires.

    ``agent`` is a ``crm.ai.dead.lead.agent`` record — it carries the env and
    the manager-configured ``elapsed_days_threshold``.
    """

    def __init__(self, agent):
        agent.ensure_one()
        self.agent = agent
        self.env = agent.env

    # ------------------------------------------------------------------
    # Classification
    # ------------------------------------------------------------------
    def classify_lost(self, lead):
        """Map the lead's lost reason to its manager-configured bucket."""
        reason = lead.lost_reason_id
        return (reason.crm_ai_reason_bucket if reason else False) or DEFAULT_BUCKET

    # ------------------------------------------------------------------
    # Qualifying gates (every one must pass)
    # ------------------------------------------------------------------
    def _is_lost(self, lead):
        # Core CRM semantic (crm_lead.py): a lead is LOST when it is archived
        # (active = False) AND probability = 0.
        return (not lead.active) and lead.probability <= 0

    def _lost_on(self, lead):
        # No native "lost date": action_set_lost only archives + zeroes
        # probability; date_closed is set on WON-stage transitions, not on lost.
        # Best available proxy is date_closed (if a prior flow set it) else the
        # last write. Task 3.7 should stamp a precise lost-date for accuracy.
        return lead.date_closed or lead.write_date

    def _days_since_lost(self, lead):
        lost_on = self._lost_on(lead)
        if not lost_on:
            return None
        return (fields.Datetime.now() - lost_on).days

    def _is_qualifying(self, lead):
        """Lost, its reason is eligible for resurrection, and it is contactable
        enough to be worth drafting. Contact-channel validity is re-checked at
        send time (3.6); here we only require the lead to actually be lost and
        not opted out by its reason."""
        if not self._is_lost(lead):
            return False
        reason = lead.lost_reason_id
        if reason and not reason.crm_ai_resurrectable:
            return False
        return True

    # ------------------------------------------------------------------
    # Trigger detection
    # ------------------------------------------------------------------
    def detect_trigger(self, lead):
        """Return a trigger dict for a qualifying lead, else None."""
        if not self._is_qualifying(lead):
            return None
        # Triggers are tried in priority order; the first that fires wins.
        return (
            self._detect_elapsed_time(lead)
            or self._detect_champion_moved(lead)
            or self._detect_price_drop(lead)
        )

    def _trigger(self, ttype, lead, **detail):
        return {
            "type": ttype,
            "lead_id": lead.id,
            "reason_bucket": self.classify_lost(lead),
            **detail,
        }

    def _detect_elapsed_time(self, lead):
        """Enough time has passed since the lead was lost.

        The threshold is manager-configured (agent.elapsed_days_threshold).
        Following the suite's <=0 convention, a threshold of 0 (or less) DISABLES
        this trigger — an explicit off-switch, not "fire immediately"."""
        threshold = self.agent.elapsed_days_threshold or 0
        if threshold <= 0:
            return None
        days = self._days_since_lost(lead)
        if days is None or days < threshold:
            return None
        return self._trigger(
            TRIGGER_ELAPSED_TIME, lead,
            days_elapsed=days,
            detail="%s days since closed-lost (threshold %s)" % (days, threshold))

    def _detect_champion_moved(self, lead):
        """DEFERRED — needs a contact-change signal this module does not own
        yet. Documented hook; currently never fires."""
        return None

    def _detect_price_drop(self, lead):
        """DEFERRED — needs a product/price signal resolving the original
        objection. Documented hook; currently never fires."""
        return None

# -*- coding: utf-8 -*-
"""The Campaign Agent orchestrator — FAIL-CLOSED at every gate.

Daily flow (run by the cron under the suite-wide resolver identity, or by a
manager's manual run):

  0. master toggle OFF                        → no-op
  1. send window / blackout day closed        → defer (nothing generated/sent)
     (window open → first complete any hand-off deferred from approval time)
  2. selected LLM transport unavailable       → fail closed (audited)
  3. approval required but no approvers       → one 'failed' campaign, stop
  4. no active catalog services               → fail closed (audited)
  5. SELECTION: eligible partners up to the daily limit, split across up to
     the max daily campaigns; every considered-but-skipped partner is
     recorded as a 'skipped' line with a REFERENCE-ONLY skip_reason code.
  6. GENERATION: per partner — playbooks by tag, language resolution, the
     PDPL-minimized payload, ONE grounded LLM call through the base seam,
     rejection of any service outside the active catalog, local PII merge.
  7. REVIEW ROUTING: low-confidence lines force human review; approval
     required → 'pending_approval' + notify approvers; else auto-approve.
  8. HAND-OFF: approved campaigns → one mailing.mailing + per-line
     personalized mail.mail/mailing.trace (official Email Marketing), lines
     'sent', per-partner send timestamps recorded (cooldown / monthly cap).

Config reads go through ``_cfg`` — a narrow sudo read of ONLY the
``era_crm_ai_agents_campaign.*`` ir.config_parameter namespace (proposed
registry elevation, same single-purpose category as the Compliance #7 and
Lead-Gen #8 config reads), so the engine can load manager settings while
running as the least-privilege cron identity. It reads no secret and writes
nothing.
"""
import logging
import math
from datetime import timedelta

import pytz

from odoo import _, fields

_logger = logging.getLogger(__name__)

PARAM_PREFIX = "era_crm_ai_agents_campaign."

# Map the blackout-day codes to Python weekday() numbers (Mon=0).
_WEEKDAY_NUM = {"mon": 0, "tue": 1, "wed": 2, "thu": 3,
                "fri": 4, "sat": 5, "sun": 6}

# The placeholder the LLM is instructed to use; real PII is merged over it
# LOCALLY, after the model returns (technical protocol token, not policy).
NAME_PLACEHOLDER = "{{customer_name}}"


class CampaignEngine:
    """Instantiate with the agent model: ``CampaignEngine(env['crm.ai.campaign.agent'])``."""

    def __init__(self, agent_model, unattended=False):
        self.agent = agent_model
        self.env = agent_model.env
        self.unattended = unattended

    # ------------------------------------------------------------------
    # Config (narrow sudo read of our own namespace only)
    # ------------------------------------------------------------------
    def _cfg(self, key, default=None):
        val = self.env["ir.config_parameter"].sudo().get_param(
            PARAM_PREFIX + key)
        return default if val in (None, False) else val

    def _cfg_bool(self, key):
        # Toggles are stored as the strings 'True'/'False' (never a bare
        # Python False — set_param would delete the row).
        return str(self._cfg(key, "False")).strip().lower() in ("true", "1")

    def _cfg_int(self, key, default=0):
        try:
            return int(str(self._cfg(key, default)).strip())
        except (TypeError, ValueError):
            return default

    def _cfg_float(self, key, default=0.0):
        try:
            return float(str(self._cfg(key, default)).strip())
        except (TypeError, ValueError):
            return default

    def approver_ids(self):
        raw = str(self._cfg("approver_user_ids", "") or "")
        return [int(t) for t in raw.split(",") if t.strip().isdigit()]

    # ------------------------------------------------------------------
    # Gates
    # ------------------------------------------------------------------
    def send_gate_open(self, now=None):
        """True when 'now' is inside the send window and not a blackout day,
        evaluated in the configured send timezone."""
        now = now or fields.Datetime.now()
        tz_name = str(self._cfg("send_tz", "Asia/Riyadh"))
        try:
            tz = pytz.timezone(tz_name)
        except pytz.UnknownTimeZoneError:
            # Fail closed on a broken timezone value — never guess a window.
            _logger.warning("Campaign agent: unknown send_tz %r; deferring.",
                            tz_name)
            return False
        local = pytz.utc.localize(now).astimezone(tz)
        blackout = {d.strip().lower()
                    for d in str(self._cfg("blackout_days", "")).split(",")
                    if d.strip()}
        if local.weekday() in {_WEEKDAY_NUM[d] for d in blackout
                               if d in _WEEKDAY_NUM}:
            return False
        hour = local.hour + local.minute / 60.0
        start = self._cfg_float("send_window_start", 9.0)
        end = self._cfg_float("send_window_end", 17.0)
        return start <= hour < end

    def _transport_gate(self):
        """(ok, reason) — fail closed when the selected transport can't run."""
        transport = str(self._cfg("llm_transport", "era_ai_accounts"))
        if transport == "era_ai_accounts":
            if not self.agent._era_ai_accounts_available():
                return False, "era_ai_accounts_unavailable"
        else:
            agent_rec = self.agent._get_agent_record()
            if agent_rec.transport != "api":
                return False, "transport_mismatch"
        return True, "ok"

    # ------------------------------------------------------------------
    # PDPL consent (Compliance layer resolved via registry check — soft dep)
    # ------------------------------------------------------------------
    def _consent_check(self, partner):
        """(ok, skip_reason). Only called when the PDPL guard is ON.

        The Compliance layer (#1) is runtime-optional: when its consent model
        is absent the partner is SKIPPED (fail closed, 'no_consent_guard') —
        we never invent a consent decision ourselves."""
        if "crm.ai.consent" not in self.env:
            return False, "no_consent_guard"
        if not self.env["crm.ai.consent"].has_consent(partner, "marketing"):
            return False, "no_consent"
        return True, ""

    # ------------------------------------------------------------------
    # Selection helpers
    # ------------------------------------------------------------------
    def _today_start_utc(self):
        """Midnight 'today' in the send timezone, as naive UTC — the boundary
        for all per-day counters (LLM cap, daily limit, campaigns/day)."""
        tz_name = str(self._cfg("send_tz", "Asia/Riyadh"))
        try:
            tz = pytz.timezone(tz_name)
        except pytz.UnknownTimeZoneError:
            tz = pytz.utc
        local_now = pytz.utc.localize(fields.Datetime.now()).astimezone(tz)
        local_midnight = tz.localize(
            local_now.replace(tzinfo=None).replace(
                hour=0, minute=0, second=0, microsecond=0))
        return local_midnight.astimezone(pytz.utc).replace(tzinfo=None)

    def _skip_reason_for(self, partner, month_start, cooldown_start):
        """Return the reference-only skip code for *partner*, or '' if
        eligible. Order: absolute stops first (suppression), then caps, then
        consent — the first hit wins."""
        Line = self.env["crm.ai.campaign.line"]
        if self.env["crm.ai.campaign.suppression"]._matches(partner):
            return "suppressed"
        sent_domain = [("partner_id", "=", partner.id),
                       ("state", "=", "sent"),
                       ("sent_date", "!=", False)]
        cap = self._cfg_int("monthly_frequency_cap", 2)
        if cap > 0 and Line.search_count(
                sent_domain + [("sent_date", ">=", month_start)]) >= cap:
            return "monthly_cap"
        cooldown = self._cfg_int("cooldown_days", 30)
        if cooldown > 0 and Line.search_count(
                sent_domain + [("sent_date", ">=", cooldown_start)], limit=1):
            return "cooldown"
        if self._cfg_bool("pdpl_guard_enabled"):
            ok, reason = self._consent_check(partner)
            if not ok:
                return reason
        return ""

    # ------------------------------------------------------------------
    # Payload / prompt building
    # ------------------------------------------------------------------
    def _resolve_lang(self, partner):
        plang = (partner.lang or "").lower()
        if plang.startswith("ar"):
            return "ar"
        if plang.startswith("en"):
            return "en"
        default = str(self._cfg("default_lang", "ar"))
        return default if default in ("ar", "en") else "ar"

    def _playbooks_for(self, partner):
        """Active playbooks matching any of the partner's tags, in sequence
        order."""
        if not partner.category_id:
            return self.env["crm.ai.campaign.playbook"]
        return self.env["crm.ai.campaign.playbook"].search([
            ("trigger_tag_ids", "in", partner.category_id.ids),
        ])

    def _services_for(self, partner):
        """The ACTIVE catalog subset offered for this partner: services whose
        target tags intersect the partner's tags, plus untargeted services
        (suitable for any segment)."""
        Catalog = self.env["crm.ai.campaign.service.catalog"]
        return Catalog.search([
            "|",
            ("target_tag_ids", "=", False),
            ("target_tag_ids", "in", partner.category_id.ids or [-1]),
        ])

    def _prior_service_ids(self, partner):
        """Identifiers of services already pitched-and-sent to this partner —
        identifiers only, per the audit discipline."""
        lines = self.env["crm.ai.campaign.line"].search([
            ("partner_id", "=", partner.id),
            ("state", "=", "sent"),
            ("matched_service_id", "!=", False),
        ])
        return lines.mapped("matched_service_id").ids

    def _build_payload(self, line, pdpl_on):
        """The customer profile handed to the LLM.

        PDPL guard ON → DATA MINIMIZATION: business attributes only, under an
        opaque per-line reference. NEVER name / email / phone / any direct
        identifier. Real PII is merged into the draft LOCALLY afterwards.

        PDPL guard OFF → minimization is NOT enforced (operator's explicit,
        documented choice): the display name and city are included for richer
        personalization. Email/phone are STILL never sent — they carry no
        drafting value on any path."""
        partner = line.partner_id
        payload = {
            "partner_ref": "line:%d" % line.id,
            "industry": partner.industry_id.name or "",
            "segments": partner.category_id.mapped("name"),
            "is_company": partner.is_company,
            "country": partner.country_id.code or "",
            "prior_service_ids": self._prior_service_ids(partner),
            "language": line.lang,
        }
        if not pdpl_on:
            payload.update({
                "name": partner.display_name,
                "city": partner.city or "",
            })
        return payload

    @staticmethod
    def _merge_pii(text, partner):
        """Replace the drafting placeholder with the real name — LOCALLY, in
        Odoo, after the LLM returned."""
        return (text or "").replace(NAME_PLACEHOLDER, partner.name or "")

    # ------------------------------------------------------------------
    # The daily run
    # ------------------------------------------------------------------
    def run(self):
        # Gate 0 — master toggle.
        if not self._cfg_bool("enabled"):
            return {"status": "disabled"}

        # Gate 1 — send window / blackout day: defer everything.
        if not self.send_gate_open():
            self._audit("other", after={"event": "campaign_run_deferred",
                                        "reason": "send_gate_closed"})
            return {"status": "deferred"}

        # Window is open: first complete hand-offs deferred at approval time.
        deferred = self.env["crm.ai.campaign"].search(
            [("state", "=", "approved")])
        for campaign in deferred:
            self.handoff(campaign)

        # Gate 2 — LLM transport (era_ai_accounts is runtime-optional).
        ok, reason = self._transport_gate()
        if not ok:
            self._audit("blocked", after={"event": "campaign_run_blocked",
                                          "reason": reason})
            return {"status": "transport_unavailable", "reason": reason}

        # Gate 3 — approval config (fail closed on an empty approver list).
        require_approval = self._cfg_bool("require_human_approval")
        approvers = self.approver_ids()
        if require_approval and not approvers:
            campaign = self._new_campaign(suffix=_("(failed)"))
            campaign.state = "failed"
            self._audit("blocked", record=campaign,
                        after={"event": "campaign_run_blocked",
                               "reason": "no_approvers_configured"})
            return {"status": "no_approvers", "campaign_ids": campaign.ids}

        # Gate 4 — grounding: no active catalog, nothing to match.
        if not self.env["crm.ai.campaign.service.catalog"].search_count([]):
            self._audit("blocked", after={"event": "campaign_run_blocked",
                                          "reason": "empty_service_catalog"})
            return {"status": "no_catalog"}

        # ------------------------------------------------ selection ----
        today_start = self._today_start_utc()
        now = fields.Datetime.now()
        month_start = now.replace(day=1, hour=0, minute=0,
                                  second=0, microsecond=0)
        cooldown_start = now - timedelta(
            days=self._cfg_int("cooldown_days", 30))

        Line = self.env["crm.ai.campaign.line"]
        Campaign = self.env["crm.ai.campaign"]

        daily_limit = self._cfg_int("daily_limit", 50)
        max_campaigns = self._cfg_int("max_daily_campaigns", 5)
        # Today's already-created work counts against both day caps, so a
        # second run on the same day can only fill what is left.
        used_today = Line.search_count([
            ("create_date", ">=", today_start), ("state", "!=", "skipped")])
        campaigns_today = Campaign.search_count(
            [("create_date", ">=", today_start)])
        remaining_partners = max(0, daily_limit - used_today)
        remaining_campaigns = max(0, max_campaigns - campaigns_today)
        if not remaining_partners or not remaining_campaigns:
            self._audit("other", after={"event": "campaign_run_noop",
                                        "reason": "daily_limits_reached"})
            return {"status": "daily_limits_reached"}

        # Don't re-process partners already WORKED today (a pending/approved/
        # skipped line). Partners merely SENT-to today flow through the normal
        # gates instead, so their exclusion is recorded with a reason
        # (cooldown / monthly cap) rather than silently dropped.
        considered_today = Line.search([
            ("create_date", ">=", today_start),
            ("state", "in", ("pending", "approved", "skipped")),
        ]).mapped("partner_id").ids
        candidates = self.env["res.partner"].search(
            [("email", "!=", False), ("id", "not in", considered_today)],
            order="id")

        eligible, skipped = [], []
        for partner in candidates:
            if len(eligible) >= remaining_partners:
                break
            reason = self._skip_reason_for(partner, month_start,
                                           cooldown_start)
            if reason:
                skipped.append((partner, reason))
                if reason in ("no_consent", "no_consent_guard"):
                    # PDPL: every consent denial is audited — reference +
                    # reason code only, no personal data (Rule 20).
                    self._audit("blocked", record=partner,
                                after={"event": "consent_denied",
                                       "partner_id": partner.id,
                                       "reason": reason})
            else:
                eligible.append(partner)

        # ------------------------------------------------ campaigns ----
        chunk = max(1, math.ceil(daily_limit / max_campaigns))
        n_campaigns = min(remaining_campaigns,
                          max(1, math.ceil(len(eligible) / chunk)))
        campaigns = Campaign.browse()
        for i in range(n_campaigns):
            campaigns |= self._new_campaign(
                suffix="#%d" % (campaigns_today + i + 1))
        # Skipped partners are recorded on the first campaign (codes only).
        for partner, reason in skipped:
            Line.create({"campaign_id": campaigns[0].id,
                         "partner_id": partner.id,
                         "state": "skipped", "skip_reason": reason})
        if not eligible:
            campaigns.write({"state": "failed"})
            self._audit("other", record=campaigns[0],
                        after={"event": "campaign_run_no_eligible_partners",
                               "skipped": len(skipped)})
            return {"status": "no_eligible_partners",
                    "campaign_ids": campaigns.ids,
                    "skipped": len(skipped)}

        # ---------------------------------------------- generation ----
        llm_cap = self._cfg_int("llm_daily_call_cap", 200)
        calls_today = Line.search_count([
            ("llm_called", "=", True), ("create_date", ">=", today_start)])
        for idx, partner in enumerate(eligible):
            campaign = campaigns[min(idx // chunk, len(campaigns) - 1)]
            line = Line.create({
                "campaign_id": campaign.id,
                "partner_id": partner.id,
                "lang": self._resolve_lang(partner),
            })
            if llm_cap > 0 and calls_today >= llm_cap:
                # Fail closed: the cap halts further GENERATION for the day.
                line.write({"state": "skipped",
                            "skip_reason": "llm_cap_reached"})
                continue
            self._generate_line(line)
            if line.llm_called:
                calls_today += 1

        # -------------------------------------------- review routing ----
        threshold = self._cfg_float("confidence_threshold", 0.7)
        results = {"status": "ok", "campaign_ids": campaigns.ids,
                   "skipped": len(skipped), "sent": 0,
                   "pending_approval": 0, "failed": 0}
        for campaign in campaigns:
            live = campaign.line_ids.filtered(lambda l: l.state == "pending")
            if not live:
                campaign.state = "failed"
                results["failed"] += 1
                continue
            forced_review = any(
                l.match_confidence < threshold for l in live)
            if require_approval or forced_review:
                if not approvers:
                    # Forced review with nobody able to review → fail closed.
                    campaign.state = "failed"
                    self._audit("blocked", record=campaign,
                                after={"event": "campaign_failed",
                                       "reason": "review_forced_no_approvers"})
                    results["failed"] += 1
                    continue
                campaign.state = "pending_approval"
                self._notify_approvers(campaign, approvers)
                self._audit("approval_requested", record=campaign,
                            after={"event": "campaign_pending_approval",
                                   "campaign_id": campaign.id,
                                   "partner_count": campaign.partner_count,
                                   "date": str(campaign.date)})
                results["pending_approval"] += 1
            else:
                campaign.state = "approved"
                live.write({"state": "approved"})
                self._audit("other", record=campaign,
                            after={"event": "campaign_auto_approved",
                                   "reason": "approval_not_required"})
                self.handoff(campaign)
                results["sent"] += 1
        return results

    def _new_campaign(self, suffix=""):
        date = fields.Date.context_today(self.agent)
        return self.env["crm.ai.campaign"].create({
            "name": (_("AI Campaign %(date)s %(suffix)s",
                       date=date, suffix=suffix)).strip(),
            "date": date,
        })

    def _generate_line(self, line):
        """One partner: playbooks → minimized payload → ONE grounded LLM call
        → validation → local PII merge. Failures skip the line with a code."""
        partner = line.partner_id
        pdpl_on = self._cfg_bool("pdpl_guard_enabled")
        services = self._services_for(partner)
        if not services:
            line.write({"state": "skipped",
                        "skip_reason": "no_matching_service"})
            return
        playbooks = self._playbooks_for(partner)
        line.write({
            "applied_playbook_ids": [(6, 0, playbooks.ids)],
            "llm_called": True,  # counts against the cap even if the call dies
        })
        reply = self.agent._llm_match_and_draft(
            payload=self._build_payload(line, pdpl_on),
            services=[{
                "id": s.id, "name": s.name, "type": s.service_type,
                "category": s.category_id.name or "",
                "description": s.description or "",
            } for s in services],
            instructions=[p.instruction for p in playbooks if p.instruction],
            lang=line.lang,
            # With the guard ON the payload is already minimized (no PII to
            # redact). With it OFF the partner name rides in the payload, so
            # hand the BASE guard the record for its own redaction layer.
            record=None if pdpl_on else partner,
            unattended=self.unattended,
        )
        if not reply:
            line.write({"state": "skipped",
                        "skip_reason": "llm_reply_invalid"})
            return
        if reply["service_id"] not in services.ids:
            # Grounding: the model picked something outside the ACTIVE
            # catalog subset it was offered → reject, never send.
            line.write({"state": "skipped",
                        "skip_reason": "invalid_service"})
            self._audit("blocked", record=line.campaign_id,
                        after={"event": "hallucinated_service_rejected",
                               "line_id": line.id,
                               "service_id": reply["service_id"]})
            return
        line.write({
            "matched_service_id": reply["service_id"],
            "match_confidence": reply["match_confidence"],
            "generated_subject": self._merge_pii(reply["subject"], partner),
            "generated_body": self._merge_pii(reply["body"], partner),
        })

    def _notify_approvers(self, campaign, approver_ids):
        users = self.env["res.users"].browse(approver_ids).exists()
        if users:
            campaign.message_post(
                body=_("Campaign '%(name)s' is ready for review "
                       "(%(count)d recipients).",
                       name=campaign.name, count=campaign.partner_count),
                partner_ids=users.partner_id.ids,
            )

    # ------------------------------------------------------------------
    # Final hand-off — official Email Marketing
    # ------------------------------------------------------------------
    def handoff(self, campaign):
        """Hand ONE approved campaign to mailing.mailing. Suppression is
        re-checked per line at this last moment (an entry added after
        selection still wins). Records the per-partner send timestamp that
        drives the cooldown and the monthly frequency cap."""
        if campaign.state != "approved":
            return False
        Suppression = self.env["crm.ai.campaign.suppression"]
        payloads = []
        for line in campaign.line_ids.filtered(
                lambda l: l.state == "approved"):
            partner = line.partner_id
            if not partner.email or Suppression._matches(partner):
                line.write({"state": "skipped",
                            "skip_reason": "suppressed_at_handoff"})
                continue
            payloads.append({
                "line": line,
                "subject": line.generated_subject,
                "body": line.generated_body,
                "email_to": partner.email_formatted or partner.email,
                "partner_id": partner.id,
            })
        if not payloads:
            campaign.write({"state": "failed"})
            self._audit("blocked", record=campaign,
                        after={"event": "campaign_handoff_empty"})
            return False
        sender = self._sender_address()
        if not sender:
            # Fail closed: never send from an undefined address.
            campaign.write({"state": "failed"})
            self._audit("blocked", record=campaign,
                        after={"event": "campaign_handoff_blocked",
                               "reason": "no_sender_address"})
            return False
        mailing = campaign._handoff_create_mailing(payloads, sender)
        now = fields.Datetime.now()
        for payload in payloads:
            payload["line"].write({"state": "sent", "sent_date": now})
        campaign.write({"state": "sent", "mailing_id": mailing.id})
        self._audit("send", record=campaign, after={
            "event": "campaign_handed_off",
            "mailing_id": mailing.id,
            "recipient_count": len(payloads),
            "partner_ids": [p["partner_id"] for p in payloads],
        })
        return mailing

    def _sender_address(self):
        """The From address for campaign emails: the configured setting, else
        the company's address, else the running user's. Empty → the hand-off
        fails closed (never send from an undefined address)."""
        configured = str(self._cfg("email_from", "") or "").strip()
        return (configured
                or self.env.company.email_formatted
                or self.env.user.email_formatted
                or "")

    # ------------------------------------------------------------------
    def _audit(self, event_type, record=None, after=None):
        """Reference-only audit rows (Rule 20) via the base append-only log.

        FAIL-OPEN for logging: an audit-write failure must never abort a run
        (and can never cause a send — sends have their own gates). The
        exception is sanitized to its class name only before logging."""
        try:
            agent_rec = self.agent._get_agent_record()
            self.env["crm.ai.audit.log"].log(
                event_type, agent_rec, record, None, after)
        except Exception as exc:  # noqa: BLE001 - logging must not kill a run
            _logger.warning(
                "Campaign agent: audit write failed (%s) for event %r",
                type(exc).__name__, event_type)

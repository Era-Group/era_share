# -*- coding: utf-8 -*-
"""The daily campaign and its per-partner lines.

A campaign is built INTERNALLY by the agent (selection → LLM matching →
drafting → review routing); only the FINAL hand-off touches Odoo's official
Email Marketing: one ``mailing.mailing`` per approved campaign, with one
personalized ``mail.mail`` (+ ``mailing.trace``) per approved line, delivered
by the standard outgoing-mail queue. Per-partner subjects/bodies are the whole
point of the agent, and a single mailing body cannot express them — so the
hand-off produces the same object graph mass_mailing itself produces when
sending (mailing + traces + mails), keeping stats inside Email Marketing.

AUDIT DISCIPLINE: audit rows and skip_reason values carry references and
identifiers only (partner ids, counts, config keys) — never a partner's name,
email or phone in free text.
"""
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# States a campaign may only leave through the approval actions (or the
# auto-approval path when human approval is configured off).
APPROVAL_LOCKED_STATES = ("pending_approval",)


class CrmAiCampaign(models.Model):
    _name = "crm.ai.campaign"
    _description = "CRM AI Daily Campaign"
    _inherit = ["mail.thread"]
    _order = "date desc, id desc"

    name = fields.Char(required=True)
    date = fields.Date(
        default=fields.Date.context_today,
        required=True,
    )
    state = fields.Selection(
        selection=[
            ("in_progress", "In Progress"),
            ("ready", "Ready"),
            ("pending_approval", "Pending Approval"),
            ("approved", "Approved"),
            ("sent", "Sent"),
            ("rejected", "Rejected"),
            ("failed", "Failed"),
        ],
        default="in_progress",
        required=True,
        copy=False,
        tracking=True,
    )
    line_ids = fields.One2many(
        comodel_name="crm.ai.campaign.line",
        inverse_name="campaign_id",
        string="Lines",
    )
    partner_count = fields.Integer(
        compute="_compute_partner_count",
        help="Number of partners actually targeted (skipped lines excluded).",
    )
    mailing_id = fields.Many2one(
        comodel_name="mailing.mailing",
        string="Email Marketing Mailing",
        readonly=True,
        copy=False,
        help="The official Email Marketing record this campaign was handed "
             "to at the final step.",
    )
    # PLACEHOLDER for future A/B testing — intentionally inert today. Kept as a
    # plain Char so campaigns can be stamped with a variant key later without a
    # schema change; no logic reads it yet.
    ab_variant = fields.Char(string="A/B Variant (future)", copy=False)

    @api.depends("line_ids.state")
    def _compute_partner_count(self):
        for campaign in self:
            campaign.partner_count = len(
                campaign.line_ids.filtered(lambda l: l.state != "skipped"))

    # ------------------------------------------------------------------
    # Approval enforcement (Rule 19 — server-side, not just buttons)
    # ------------------------------------------------------------------
    @api.model
    def _approver_ids(self):
        """The configured approver user ids (empty list = none configured).

        Read through the engine's narrow config reader so it works for any
        AI user (the value itself is just a list of user ids — no secret).
        """
        from odoo.addons.era_crm_ai_agents_campaign.services.campaign_engine import (
            CampaignEngine,
        )
        return CampaignEngine(self.env["crm.ai.campaign.agent"]).approver_ids()

    def _ensure_user_is_approver(self):
        approvers = self._approver_ids()
        if self.env.user.id not in approvers:
            raise UserError(_(
                "Only a configured campaign approver may approve or reject a "
                "campaign. Ask an AI Agents Manager to add you in Campaign "
                "Agent Settings."))

    def write(self, vals):
        """Server-side guard on the approval transition.

        Record rules cannot express 'only approvers may move a campaign OUT of
        pending_approval' (the cron identity must still write campaigns in
        every other state), so the enforcement lives here: any transition out
        of an approval-locked state requires membership in the configured
        approver list. The superuser bypass (env.su) is deliberate — it keeps
        module upgrades/migrations working; normal agent code never runs su.
        """
        if "state" in vals and not self.env.su:
            locked = self.filtered(lambda c: c.state in APPROVAL_LOCKED_STATES)
            if locked and vals["state"] not in APPROVAL_LOCKED_STATES:
                self._ensure_user_is_approver()
        return super().write(vals)

    # ------------------------------------------------------------------
    # Approval actions (buttons / server actions)
    # ------------------------------------------------------------------
    def action_approve(self):
        """Approver signs off → final hand-off to Email Marketing.

        If the send window / blackout gate is currently closed, the campaign
        stays 'approved' and the daily cron completes the hand-off inside the
        next allowed window (defer, never send outside the window).
        """
        from odoo.addons.era_crm_ai_agents_campaign.services.campaign_engine import (
            CampaignEngine,
        )
        self._ensure_user_is_approver()
        engine = CampaignEngine(self.env["crm.ai.campaign.agent"])
        for campaign in self:
            if campaign.state != "pending_approval":
                raise UserError(_(
                    "Campaign '%(name)s' is not pending approval.",
                    name=campaign.name))
            campaign.state = "approved"
            campaign.line_ids.filtered(
                lambda l: l.state == "pending").write({"state": "approved"})
            self.env["crm.ai.audit.log"].log(
                "other", record=campaign,
                after={"event": "campaign_approved",
                       "approver_uid": self.env.user.id})
            if engine.send_gate_open():
                engine.handoff(campaign)
            else:
                campaign.message_post(body=_(
                    "Approved outside the allowed send window — the hand-off "
                    "is deferred to the next scheduled run inside the window."))
        return True

    def action_reject(self):
        """Approver rejects → nothing is ever sent from this campaign."""
        self._ensure_user_is_approver()
        for campaign in self:
            if campaign.state != "pending_approval":
                raise UserError(_(
                    "Campaign '%(name)s' is not pending approval.",
                    name=campaign.name))
            campaign.line_ids.filtered(
                lambda l: l.state in ("pending", "approved")
            ).write({"state": "rejected"})
            campaign.state = "rejected"
            self.env["crm.ai.audit.log"].log(
                "other", record=campaign,
                after={"event": "campaign_rejected",
                       "approver_uid": self.env.user.id})
        return True

    # ------------------------------------------------------------------
    # Final hand-off primitives (called by the engine)
    # ------------------------------------------------------------------
    def _handoff_create_mailing(self, line_payloads, email_from):
        """Create the official Email Marketing artifacts for ONE campaign.

        PROPOSED SUDO ELEVATION (single-purpose, pending registry approval):
        ``mailing.mailing``/``mailing.trace`` creation requires the Email
        Marketing user group and ``mail.mail`` requires base.group_system, so
        the least-privilege cron identity cannot perform the hand-off unaided.
        This helper elevates for EXACTLY the artifact creation of an
        already-approved campaign and nothing else: one mailing.mailing, plus
        one mail.mail + mailing.trace per approved line. Eligibility, approval,
        suppression and every other decision are settled BEFORE this runs,
        under the real user. Results are rebound to the caller's env.

        :param line_payloads: list of dicts
            {'line': crm.ai.campaign.line, 'subject': str, 'body': str,
             'email_to': str, 'partner_id': int}
        :param email_from: the resolved sender address (the engine fails
            closed before calling this when none is configured/derivable).
        :returns: the created mailing.mailing (caller's env).
        """
        self.ensure_one()
        if self.state != "approved":
            raise UserError(_(
                "Internal error: hand-off requested for campaign "
                "'%(name)s' which is not approved.", name=self.name))
        partner_ids = [p["partner_id"] for p in line_payloads]
        sudo_env = self.sudo().env
        mailing = sudo_env["mailing.mailing"].create({
            "subject": self.name,
            "email_from": email_from,
            "mailing_model_id": sudo_env["ir.model"]._get_id("res.partner"),
            "mailing_domain": repr([("id", "in", partner_ids)]),
            "body_arch": "<p>Per-recipient AI-personalized content — see the "
                         "individual emails linked to this mailing.</p>",
            "body_html": "<p>Per-recipient AI-personalized content — see the "
                         "individual emails linked to this mailing.</p>",
            # We generate the personalized emails ourselves below; the mailing
            # record is the official container carrying the traces/stats.
            "state": "done",
        })
        for payload in line_payloads:
            sudo_env["mail.mail"].create({
                "subject": payload["subject"],
                "body_html": payload["body"],
                "email_from": email_from,
                "email_to": payload["email_to"],
                "model": "res.partner",
                "res_id": payload["partner_id"],
                "mailing_id": mailing.id,
                "state": "outgoing",
                "auto_delete": False,
                "mailing_trace_ids": [(0, 0, {
                    "model": "res.partner",
                    "res_id": payload["partner_id"],
                    "mass_mailing_id": mailing.id,
                    "email": payload["email_to"],
                })],
            })
        return mailing.with_env(self.env)


class CrmAiCampaignLine(models.Model):
    _name = "crm.ai.campaign.line"
    _description = "CRM AI Campaign Line (one targeted partner)"
    _order = "id"

    campaign_id = fields.Many2one(
        comodel_name="crm.ai.campaign",
        required=True,
        ondelete="cascade",
        index=True,
    )
    partner_id = fields.Many2one(
        comodel_name="res.partner",
        required=True,
        ondelete="cascade",
        index=True,
    )
    matched_service_id = fields.Many2one(
        comodel_name="crm.ai.campaign.service.catalog",
        string="Matched Service",
        ondelete="set null",
    )
    applied_playbook_ids = fields.Many2many(
        comodel_name="crm.ai.campaign.playbook",
        relation="crm_ai_campaign_line_playbook_rel",
        string="Applied Playbooks",
    )
    match_confidence = fields.Float(
        help="LLM-reported confidence (0..1) that the matched service fits "
             "this partner. Lines below the configured threshold are always "
             "forced into human review.",
    )
    lang = fields.Selection(
        selection=[("ar", "Arabic"), ("en", "English")],
        help="Resolved drafting language: the partner's language when set, "
             "else the configured default.",
    )
    generated_subject = fields.Char()
    generated_body = fields.Html(sanitize="email_outgoing")
    state = fields.Selection(
        selection=[
            ("pending", "Pending"),
            ("approved", "Approved"),
            ("rejected", "Rejected"),
            ("sent", "Sent"),
            ("skipped", "Skipped"),
        ],
        default="pending",
        required=True,
        copy=False,
    )
    skip_reason = fields.Char(
        help="Reference code for why this partner was skipped "
             "(e.g. 'suppressed', 'cooldown', 'no_consent_guard'). Codes "
             "only — never personal data.",
    )
    # Internal bookkeeping (not in the spec, needed by the caps):
    llm_called = fields.Boolean(
        help="True once an LLM call was attempted for this line — the counter "
             "behind the daily LLM call cap.",
    )
    sent_date = fields.Datetime(
        readonly=True,
        copy=False,
        help="When the hand-off queued this partner's email. Drives the "
             "per-partner cooldown and the monthly frequency cap.",
    )

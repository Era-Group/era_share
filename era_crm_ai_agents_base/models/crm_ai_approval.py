# -*- coding: utf-8 -*-
"""Human approval layer: the controlled gate between agents and humans.

A queue of proposed agent outputs (e.g. an Arabic message) routed to a reviewer
who can edit, approve, or reject. On approval the requesting agent's callback
(`_ai_on_approved`, a generic hook on crm.ai.agent.mixin) is fired so the agent
performs the actual send/action — the base never hardcodes any agent's logic.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class CrmAiApproval(models.Model):
    _name = "crm.ai.approval"
    _description = "CRM AI Human Approval"
    _order = "create_date desc"

    agent_id = fields.Many2one(
        comodel_name="crm.ai.agent", string="Agent",
        ondelete="set null", index=True,
    )
    proposed_content = fields.Text(string="Proposed Content", required=True)
    edited_content = fields.Text(
        string="Edited Content",
        help="Optional human edit. When set, it supersedes the proposed content "
             "on approval.",
    )
    effective_content = fields.Text(
        string="Effective Content",
        compute="_compute_effective_content",
        help="What will actually be used: the edited content if any, else the "
             "proposed content.",
    )
    record_ref = fields.Reference(
        selection="_selection_target_model", string="Related Record",
    )
    reviewer_id = fields.Many2one(
        comodel_name="res.users", string="Reviewer", index=True,
    )
    state = fields.Selection(
        selection=[
            ("pending", "Pending"),
            ("approved", "Approved"),
            ("rejected", "Rejected"),
        ],
        string="Status", default="pending", required=True, index=True, copy=False,
    )
    decided_on = fields.Datetime(string="Decided On", readonly=True, copy=False)
    # The agent model that requested this, used to fire its callback on approval.
    source_model = fields.Char(string="Source Model", readonly=True)

    @api.model
    def _selection_target_model(self):
        return [(m.model, m.name) for m in self.env["ir.model"].sudo().search([])]

    @api.depends("edited_content", "proposed_content")
    def _compute_effective_content(self):
        for approval in self:
            approval.effective_content = approval.edited_content or approval.proposed_content

    def _compute_display_name(self):
        for approval in self:
            approval.display_name = _("Approval: %s", approval.agent_id.name or _("AI Agent"))

    # ------------------------------------------------------------------
    # Creation
    # ------------------------------------------------------------------
    @api.model
    def create_request(self, agent, content, record=None, reviewer=None, source_model=None):
        """Open a pending approval. Runs under the calling user (no sudo)."""
        vals = {
            "agent_id": agent.id if agent else False,
            "proposed_content": content,
            "state": "pending",
            "source_model": source_model,
        }
        if reviewer:
            vals["reviewer_id"] = reviewer.id
        ref = self._record_ref(record)
        if ref:
            vals["record_ref"] = ref
        return self.create(vals)

    # ------------------------------------------------------------------
    # Decisions
    # ------------------------------------------------------------------
    def action_approve(self):
        for approval in self:
            if approval.state != "pending":
                raise UserError(_("Only pending requests can be approved."))
            approval.write({
                "state": "approved",
                "reviewer_id": self.env.uid,
                "decided_on": fields.Datetime.now(),
            })
            self.env["crm.ai.audit.log"].log(
                "other", approval.agent_id, approval.record_ref or None,
                {"state": "pending"}, {"state": "approved", "approval_id": approval.id},
            )
            approval._fire_callback()
        return True

    def action_reject(self):
        for approval in self:
            if approval.state != "pending":
                raise UserError(_("Only pending requests can be rejected."))
            approval.write({
                "state": "rejected",
                "reviewer_id": self.env.uid,
                "decided_on": fields.Datetime.now(),
            })
            self.env["crm.ai.audit.log"].log(
                "other", approval.agent_id, approval.record_ref or None,
                {"state": "pending"}, {"state": "rejected", "approval_id": approval.id},
            )
        return True

    def _fire_callback(self):
        """Fire the requesting agent's generic approval hook (it overrides it)."""
        self.ensure_one()
        if self.source_model and self.source_model in self.env:
            self.env[self.source_model]._ai_on_approved(self)
        return True

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _record_ref(record):
        if record is not None and getattr(record, "_name", False) and record.id:
            return "%s,%s" % (record._name, record.id)
        return False

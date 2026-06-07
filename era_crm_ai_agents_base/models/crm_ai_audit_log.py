# -*- coding: utf-8 -*-
"""Append-only critical audit log (Rule 20).

Entries are immutable once written: write()/unlink() are blocked for everyone
except the system admin (PDPL redaction). The log() helper sudo-CREATES one row
(approved elevation) so the no-sudo mixin can always log, even for users who
have no ACL on this model.
"""
import json

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class CrmAiAuditLog(models.Model):
    _name = "crm.ai.audit.log"
    _description = "CRM AI Critical Audit Log"
    _order = "create_date desc"
    _rec_name = "event_type"

    event_type = fields.Selection(
        selection=[
            # Business events (Notion 0.6 spec)
            ("send", "Send"),
            ("delete", "Delete"),
            ("role_change", "Role Change"),
            ("external_contact", "External Contact"),
            # Infrastructure events emitted by crm.ai.agent.mixin
            ("llm_call_failed", "LLM Call Failed"),
            ("cost_cap_exceeded", "Cost Cap Exceeded"),
            ("approval_requested", "Approval Requested"),
            # AI Compliance Guard decisions (native AI egress)
            ("ai_request", "AI Request (allowed)"),
            ("blocked", "AI Request Blocked"),
            ("other", "Other"),
        ],
        string="Event Type",
        required=True,
        index=True,
    )
    agent_id = fields.Many2one(
        comodel_name="crm.ai.agent", string="Agent",
        ondelete="set null", index=True,
    )
    user_id = fields.Many2one(
        comodel_name="res.users", string="User",
        ondelete="set null", index=True,
        default=lambda self: self.env.uid,
    )
    model_ref = fields.Reference(
        selection="_selection_target_model", string="Related Record",
    )
    value_before = fields.Text(string="Value Before")
    value_after = fields.Text(string="Value After")
    # create_date is provided automatically by Odoo.

    @api.model
    def _selection_target_model(self):
        return [(m.model, m.name) for m in self.env["ir.model"].sudo().search([])]

    # ------------------------------------------------------------------
    # Logging (approved sudo elevation: create-only)
    # ------------------------------------------------------------------
    @api.model
    def log(self, event_type, agent=None, record=None, before=None, after=None):
        """Append one immutable audit entry. Sudo-creates so logging always
        succeeds (Rule 20) regardless of the caller's ACL. Create only."""
        vals = {
            "event_type": event_type,
            "agent_id": agent.id if agent else False,
            "user_id": self.env.uid,
            "value_before": self._to_text(before),
            "value_after": self._to_text(after),
        }
        ref = self._record_ref(record)
        if ref:
            vals["model_ref"] = ref
        return self.sudo().create(vals)

    # ------------------------------------------------------------------
    # Immutability
    # ------------------------------------------------------------------
    def write(self, vals):
        if not self._is_admin():
            raise UserError(_("Audit log entries are immutable and cannot be edited."))
        return super().write(vals)

    def unlink(self):
        if not self._is_admin():
            raise UserError(_("Audit log entries cannot be deleted."))
        return super().unlink()

    def _is_admin(self):
        return self.env.su or self.env.user.has_group("base.group_system")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _record_ref(record):
        if record is not None and getattr(record, "_name", False) and record.id:
            return "%s,%s" % (record._name, record.id)
        return False

    @staticmethod
    def _to_text(value):
        if value is None:
            return False
        if isinstance(value, (dict, list)):
            return json.dumps(value, default=str, ensure_ascii=False)
        return str(value)

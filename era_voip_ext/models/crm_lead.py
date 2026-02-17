from odoo import api, fields, models


class CrmLead(models.Model):
    _inherit = "crm.lead"

    voip_call_count = fields.Integer(
        string="VoIP Calls",
        compute="_compute_voip_call_count",
    )

    def _compute_voip_call_count(self):
        Call = self.env["voip.call"]
        if not self.ids:
            for lead in self:
                lead.voip_call_count = 0
            return

        if "res_id" in Call._fields and "res_model" in Call._fields:
            data = Call._read_group(
                [
                    ("res_model", "=", "crm.lead"),
                    ("res_id", "in", self.ids),
                ],
                groupby=["res_id"],
                aggregates=["__count"],
            )
            counts = {res_id: count for res_id, count in data}
            for lead in self:
                lead.voip_call_count = counts.get(lead.id, 0)
            return

        if "lead_id" in Call._fields:
            data = Call._read_group(
                [("lead_id", "in", self.ids)],
                groupby=["lead_id"],
                aggregates=["__count"],
            )
            counts = {lead.id: count for lead, count in data if lead}
            for lead in self:
                lead.voip_call_count = counts.get(lead.id, 0)
            return

        if "opportunity_id" in Call._fields:
            data = Call._read_group(
                [("opportunity_id", "in", self.ids)],
                groupby=["opportunity_id"],
                aggregates=["__count"],
            )
            counts = {
                opportunity.id: count for opportunity, count in data if opportunity
            }
            for lead in self:
                lead.voip_call_count = counts.get(lead.id, 0)
            return

        if "partner_id" in Call._fields:
            partner_ids = self.mapped("partner_id").ids
            if not partner_ids:
                for lead in self:
                    lead.voip_call_count = 0
                return
            data = Call._read_group(
                [("partner_id", "in", partner_ids)],
                groupby=["partner_id"],
                aggregates=["__count"],
            )
            counts = {
                partner.id: count for partner, count in data if partner
            }
            for lead in self:
                lead.voip_call_count = counts.get(lead.partner_id.id, 0)
            return

        for lead in self:
            lead.voip_call_count = 0

    def action_view_voip_calls(self):
        self.ensure_one()
        Call = self.env["voip.call"]
        if "res_id" in Call._fields and "res_model" in Call._fields:
            domain = [
                ("res_model", "=", "crm.lead"),
                ("res_id", "=", self.id),
            ]
            context = {
                "default_res_model": "crm.lead",
                "default_res_id": self.id,
            }
        elif "lead_id" in Call._fields:
            domain = [("lead_id", "=", self.id)]
            context = {"default_lead_id": self.id}
        elif "opportunity_id" in Call._fields:
            domain = [("opportunity_id", "=", self.id)]
            context = {"default_opportunity_id": self.id}
        elif "partner_id" in Call._fields and self.partner_id:
            domain = [("partner_id", "=", self.partner_id.id)]
            context = {"default_partner_id": self.partner_id.id}
        else:
            domain = [("id", "=", 0)]
            context = {}
        return {
            "type": "ir.actions.act_window",
            "name": "VoIP Calls",
            "res_model": "voip.call",
            "view_mode": "list,form",
            "domain": domain,
            "context": context,
        }

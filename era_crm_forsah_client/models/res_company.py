# -*- coding: utf-8 -*-
from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    forsah_business_description = fields.Text(
        string="Business Activity Description",
        help="Free-text description of this company's business. Used by the AI "
             "to score how relevant each Forsah/Etimad tender is to the company.")

    def write(self, vals):
        changed = self.browse()
        if "forsah_business_description" in vals:
            new_value = vals.get("forsah_business_description") or ""
            changed = self.filtered(
                lambda company: (company.forsah_business_description or "") != new_value)
        res = super().write(vals)
        if changed:
            changed._reset_tender_ai_match()
        return res

    def _reset_tender_ai_match(self):
        """Clear AI match scores so the scheduler re-scores tenders against the
        new business description. Bounded to the open candidate set so the large
        expired backlog is never touched, and to the changed company's own
        tenders where the model is company-scoped (so editing one company's
        description does not wipe another company's scores).
        """
        reset_vals = {
            "ai_match_date": False,
            "ai_match_score": 0,
            "ai_match": False,
            "ai_match_reason": False,
        }
        for model_name in ("crm.forsah.client", "crm.etimad.client"):
            Model = self.env[model_name].sudo()
            domain = Model._ai_match_candidates_domain()
            if "company_id" in Model._fields:
                domain = domain + [("company_id", "in", self.ids)]
            candidates = Model.search(domain)
            if candidates:
                candidates.write(reset_vals)

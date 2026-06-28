# -*- coding: utf-8 -*-
"""Enrichment attempt — one row per provider call in a run (task 2.7).

This is the QUERYABLE substrate the per-provider dashboard needs: the run log's
``providers_tried`` JSON and the Base ``crm.ai.usage`` rows (which bill the agent,
with no provider dimension) can't be aggregated by provider. Each attempt records
the provider, whether it returned a usable result, whether it was billable, and
what it cost — so success-rate and cost can be pivoted/graphed per provider.

No raw PII: only the subject's id (via the request's partner/lead) and the
provider verdict are stored.
"""
from odoo import api, fields, models


class CrmAiEnrichmentAttempt(models.Model):
    _name = "crm.ai.enrichment.attempt"
    _description = "Enrichment Provider Attempt"
    _order = "create_date desc"

    request_id = fields.Many2one(
        "crm.ai.enrichment.request", string="Request", required=True,
        ondelete="cascade", index=True)
    provider_id = fields.Many2one(
        "crm.ai.enrichment.provider", string="Provider",
        ondelete="set null", index=True)
    # Stable snapshots so analytics survive a provider rename/delete.
    provider_name = fields.Char(string="Provider (snapshot)")
    provider_type = fields.Char(string="Type (snapshot)")

    status = fields.Char(
        help="Engine status for this attempt: success / no_data / unknown / error.")
    success = fields.Boolean(
        help="The provider returned a usable result (status = success).")
    success_unit = fields.Float(
        string="Success (unit)", compute="_compute_success_unit", store=True,
        aggregator="avg",
        help="1.0 on success else 0.0 — averaged in the pivot this IS the "
             "per-provider success rate.")
    billable = fields.Boolean(help="A real, chargeable vendor call happened.")
    cost = fields.Float(string="Cost (USD)", digits=(12, 4), aggregator="sum")

    # Subject (related from the request) — for filtering/grouping, id only.
    partner_id = fields.Many2one(
        related="request_id.partner_id", store=True, index=True)
    lead_id = fields.Many2one(
        related="request_id.lead_id", store=True, index=True)

    @api.depends("success")
    def _compute_success_unit(self):
        for attempt in self:
            attempt.success_unit = 1.0 if attempt.success else 0.0

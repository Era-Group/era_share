# -*- coding: utf-8 -*-
"""Enrichment request — the per-run log (which providers ran, what was filled).

SCAFFOLD: the model exists for the engine to write a row per run in later tasks.
PII is never stored raw here beyond the linked partner/lead; contactability
results are summarized (masked) by the engine.
"""
from odoo import fields, models


class CrmAiEnrichmentRequest(models.Model):
    _name = "crm.ai.enrichment.request"
    _description = "Enrichment Request"
    _order = "create_date desc"

    partner_id = fields.Many2one(
        "res.partner", string="Contact", ondelete="cascade", index=True)
    lead_id = fields.Many2one(
        "crm.lead", string="Lead", ondelete="cascade", index=True)
    request_type = fields.Selection(
        selection=[
            ("full", "Full enrichment"),
            ("contactability", "Contactability check only"),
            ("specific", "Specific fields only"),
        ],
        default="full", required=True)
    state = fields.Selection(
        selection=[
            ("queued", "Queued"),
            ("processing", "Processing"),
            ("done", "Completed"),
            ("failed", "All providers failed"),
            ("capped", "Stopped — cost cap"),
        ],
        default="queued", required=True)
    providers_tried = fields.Text(help="JSON: [{provider, status, billable, cost}].")
    fields_enriched = fields.Text(help="JSON: fields filled this run.")
    contactability = fields.Text(
        help="JSON: {email:{valid}, phone:{valid}, whatsapp:{exists}}. WhatsApp "
             "is True/False/None (None = unknown / not checked).")
    total_cost = fields.Float(
        string="Total cost (USD)", digits=(12, 4), readonly=True)
    attempt_ids = fields.One2many(
        "crm.ai.enrichment.attempt", "request_id", string="Provider attempts",
        readonly=True,
        help="One row per provider call in this run — the queryable detail "
             "behind the providers_tried summary.")

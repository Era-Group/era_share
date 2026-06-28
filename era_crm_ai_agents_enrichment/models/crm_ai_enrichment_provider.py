# -*- coding: utf-8 -*-
"""Enrichment provider catalog — the waterfall sources, ordered by priority.

SCAFFOLD: the model + its billing-mode contract exist; no handler logic runs
yet. Like the Lead-Gen sources, every provider is seeded/created INACTIVE and a
manager activates it once its token env var is set. The token itself is NEVER
stored — only the NAME of the environment variable that holds it (Rule 03).
"""
import os

from odoo import api, fields, models


class CrmAiEnrichmentProvider(models.Model):
    _name = "crm.ai.enrichment.provider"
    _description = "Enrichment Data Provider"
    _order = "priority, name"

    name = fields.Char(required=True)
    provider_type = fields.Selection(
        selection=[
            ("email", "Email deliverability"),
            ("phone", "Phone / HLR"),
            ("whatsapp", "WhatsApp presence (WAHA)"),
            ("company_data", "Company data"),
            ("web_search", "Web search + LLM"),
            ("custom", "Custom API"),
        ],
        required=True, default="company_data",
        help="Drives which handler runs and which fields/channels it can fill.")
    priority = fields.Integer(
        default=10, help="Waterfall order — lower runs first.")
    handler_key = fields.Char(
        help="Selects the code handler that runs this provider. provider_type is "
             "too coarse — e.g. an email FINDER and an email VERIFIER are both "
             "'email' but dispatch to different handlers. Known keys: "
             "zerobounce, hlr, waha (built so far). Empty falls back to "
             "provider_type; an unknown key means no handler -> the provider is "
             "skipped (safe).")
    active = fields.Boolean(
        default=False,
        help="Seeded OFF. A manager activates a provider once its token env var "
             "is set; an active source with a missing token is skipped.")
    fields_filled = fields.Char(
        help="Comma-separated fields/channels this provider fills "
             "(e.g. 'email' or 'whatsapp' or 'name,website,sector'). Drives which "
             "providers the waterfall picks for the fields a record still needs.")
    env_key_param = fields.Char(
        string="Token env var name",
        help="The NAME of the environment variable holding this provider's "
             "token. Rule 03: the token VALUE is never stored in code or the DB.")
    token_present = fields.Boolean(
        compute="_compute_token_present",
        help="Whether the named env var currently resolves (the value is never "
             "shown).")

    # Cost model (Rule 14) — booked to crm.ai.usage by the engine's billing hook.
    billing_mode = fields.Selection(
        selection=[
            ("per_call", "Per call — billed on every attempt (success or fail)"),
            ("per_success", "Per success — billed only when data is returned"),
            ("free", "Free — self-hosted (e.g. WAHA), no monetary cost"),
        ],
        default="per_call", required=True,
        help="How this provider bills. Default per_call: many verification APIs "
             "charge per attempt, so failed-before-success calls still cost.")
    cost_per_call = fields.Float(
        string="Cost per call (USD)", digits=(12, 4), default=0.0)
    cache_ttl_days = fields.Integer(
        string="Cache TTL (days)", default=30,
        help="Skip (and don't re-bill) a re-check of the same value within this "
             "window. WAHA is monetarily free but rate/ban-limited, so cache it "
             "too.")

    @api.depends("env_key_param")
    def _compute_token_present(self):
        for provider in self:
            provider.token_present = bool(
                provider.env_key_param and os.getenv(provider.env_key_param))

    # -- per-provider performance (task 2.7) — computed from attempt rows ------
    attempt_count = fields.Integer(
        string="Attempts", compute="_compute_stats",
        help="Total provider calls recorded for this provider.")
    success_rate = fields.Float(
        string="Success rate (%)", compute="_compute_stats",
        help="Share of attempts that returned a usable result.")
    total_spend = fields.Float(
        string="Total spend (USD)", digits=(12, 4), compute="_compute_stats",
        help="Sum of booked cost across this provider's attempts.")

    def _compute_stats(self):
        Attempt = self.env["crm.ai.enrichment.attempt"]
        stats = {}
        if self.ids:
            # sudo: a read-only global aggregate (like the Base usage sums), so
            # the figure is the TRUE company-wide rate regardless of viewer.
            for prov, count, cost_sum, succ_avg in Attempt.sudo()._read_group(
                    [("provider_id", "in", self.ids)],
                    groupby=["provider_id"],
                    aggregates=["__count", "cost:sum", "success_unit:avg"]):
                stats[prov.id] = (count, cost_sum or 0.0, succ_avg or 0.0)
        for provider in self:
            count, cost_sum, succ_avg = stats.get(provider.id, (0, 0.0, 0.0))
            provider.attempt_count = count
            provider.total_spend = cost_sum
            provider.success_rate = succ_avg * 100.0

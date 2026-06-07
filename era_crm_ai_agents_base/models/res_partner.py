# -*- coding: utf-8 -*-
"""PDPL consent on the partner — read by the AI Compliance Guard.

Before any personal data may be sent to an international AI provider (OpenAI /
Google, both US-based), the customer must have consented. The guard reads
``crm_ai_intl_processing_consent`` from every partner involved in a call and
blocks (never sends) when it is missing. Default is False — consent is opt-in and
fail-safe.

This is the minimal consent surface the base needs now; the full Compliance
Guardrail (Agent #15) builds the consent-capture / opt-out / DSAR workflow on top
of these fields later.
"""
from odoo import fields, models


class ResPartner(models.Model):
    _inherit = "res.partner"

    crm_ai_intl_processing_consent = fields.Boolean(
        string="AI Int'l Processing Consent",
        default=False,
        tracking=True,
        help="The customer consented to processing their personal data by an "
             "international AI provider (e.g. OpenAI/Google, US-based). When "
             "unticked, the AI Compliance Guard blocks any AI call carrying this "
             "partner's personal data (PDPL).",
    )
    crm_ai_consent_date = fields.Datetime(
        string="AI Consent Date",
        help="When AI international-processing consent was recorded.",
    )

# -*- coding: utf-8 -*-
"""Phone-deliverability handler — HLR lookup (task 2.4).

Verifies the record's phone number is live/reachable via an HLR-lookup vendor and
reports the verdict on the ``phone`` channel; the engine persists a definite
verdict to res.partner.mobile_valid. (This CE has no separate mobile field, so the
partner's ``phone`` is the number checked.)

is_available() requires the token env var AND a configured HLR endpoint
(era_crm_ai_agents_enrichment.hlr_url) — no canonical HLR vendor is pinned, so the
endpoint is operator config. The outbound call + consent gate live in
services/deliverability.verify_mobile.
"""
import os

from .base import ProviderHandler
from .. import deliverability


class PhoneHlrHandler(ProviderHandler):
    provider_type = "phone"

    def capabilities(self):
        return {"phone"}

    def is_available(self):
        if not (self.provider.env_key_param
                and os.getenv(self.provider.env_key_param)):
            return False
        # An HLR endpoint must be configured; absent -> cannot verify.
        return bool(self.env["ir.config_parameter"].sudo().get_param(
            "era_crm_ai_agents_enrichment.hlr_url"))

    def enrich(self, record, fields_needed):
        partner = record if record._name == "res.partner" \
            else getattr(record, "partner_id", False)
        phone = getattr(partner, "phone", False) if partner else False
        res = deliverability.verify_mobile(
            self.env, partner, phone, self.provider.env_key_param)
        status = "success" if res["status"] == "success" else (
            "error" if res["status"] == "error" else "no_data")
        return {
            "data": {},
            "channel": {"phone": res["valid"]},
            "status": status,
            "billable": res["billable"],
        }

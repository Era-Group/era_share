# -*- coding: utf-8 -*-
"""Email-deliverability handler — ZeroBounce (task 2.4).

Verifies (does NOT find) an email: it reads the record's existing email and asks
ZeroBounce whether it is deliverable, returning the verdict on the ``email``
channel. The engine persists a definite verdict to res.partner.email_valid.

is_available() requires the token env var to resolve (Rule 03). The outbound call
+ consent gate live in services/deliverability.verify_email — this class only
adapts that primitive to the uniform ProviderHandler contract.
"""
import os

from .base import ProviderHandler
from .. import deliverability


class EmailZeroBounceHandler(ProviderHandler):
    provider_type = "email"

    def capabilities(self):
        return {"email"}

    def is_available(self):
        """Token env var resolves. (Consent is per-record, checked in enrich.)"""
        return bool(self.provider.env_key_param
                    and os.getenv(self.provider.env_key_param))

    def enrich(self, record, fields_needed):
        partner = record if record._name == "res.partner" \
            else getattr(record, "partner_id", False)
        email = getattr(partner, "email", False) if partner else False
        res = deliverability.verify_email(
            self.env, partner, email, self.provider.env_key_param)
        status = "success" if res["status"] == "success" else (
            "error" if res["status"] == "error" else "no_data")
        return {
            "data": {},  # a verifier fills no field; it reports a channel verdict
            "channel": {"email": res["valid"]},
            "status": status,
            "billable": res["billable"],
        }

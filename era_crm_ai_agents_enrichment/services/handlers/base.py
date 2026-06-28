# -*- coding: utf-8 -*-
"""The uniform provider-handler contract.

Every provider (email, phone, whatsapp, web_search, company_data, custom)
implements this SAME interface, so the engine treats them identically and any one
of them — notably the gated+optional WhatsApp slot — can be deferred or cut with
zero impact on the rest of the waterfall.
"""


class ProviderHandler:
    #: matches crm.ai.enrichment.provider.provider_type
    provider_type = None

    def __init__(self, env, provider):
        self.env = env
        self.provider = provider  # crm.ai.enrichment.provider record

    def is_available(self):
        """True if this provider can run right now.

        e.g. token env var resolves (email/phone/company_data) or, for WhatsApp,
        the manager toggle is ON AND the WAHA connector is available.
        """
        raise NotImplementedError

    def capabilities(self):
        """set[str] of fields/channels this provider can fill."""
        return set()

    def enrich(self, record, fields_needed):
        """Run the provider. Return a uniform result dict:

            {
              "data": {<field>: <value>, ...},        # filled record fields
              "channel": {"whatsapp": True|False|None},  # deliverability (None=unknown)
              "status": "success" | "no_data" | "unknown" | "error",
              "billable": bool,   # did a billable API call actually happen?
            }

        The engine books cost from ``billable`` + the provider's billing_mode.
        """
        raise NotImplementedError

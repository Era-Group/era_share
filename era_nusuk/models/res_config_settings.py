# -*- coding: utf-8 -*-

from odoo import models, fields


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    # --- Regulatory validations (Nusuk Masar rules, toggleable) ----------
    nusuk_enforce_hotel_coverage = fields.Boolean(
        string="Require Hotel Coverage before Visa Submission",
        config_parameter="era_nusuk.enforce_hotel_coverage",
        default=True,
        help="Block visa submission unless the pilgrim's group has approved "
             "hotel bookings covering the full stay (Masar rule since "
             "June 2025).")
    nusuk_enforce_transport = fields.Boolean(
        string="Require Transport Movements before Visa Submission",
        config_parameter="era_nusuk.enforce_transport_before_visa",
        default=True,
        help="Block visa submission unless the group has arrival and "
             "departure movements registered.")
    nusuk_enforce_agent_quota = fields.Boolean(
        string="Enforce Agent Seasonal Quota",
        config_parameter="era_nusuk.enforce_agent_quota",
        default=False,
        help="Block visa submission when the agent has exhausted his Masar "
             "seasonal quota.")

    # --- Nusuk voucher file matching -------------------------------------
    nusuk_col_visa_fees = fields.Integer(
        string="Nusuk File: Visa Fees Column",
        config_parameter="era_nusuk.nusuk_col_visa_fees",
        default=14,
        help="1-based column index of the visa-fees column (رسوم التأشيرة) "
             "in the Nusuk voucher export.")
    nusuk_col_grand_total = fields.Integer(
        string="Nusuk File: Grand Total Column",
        config_parameter="era_nusuk.nusuk_col_grand_total",
        default=24,
        help="1-based column index of the grand-total column "
             "(المبلغ الاجمالي) in the Nusuk voucher export.")

    # --- Invoicing --------------------------------------------------------
    nusuk_service_vat_percent = fields.Float(
        string="Service Fee VAT (%)",
        config_parameter="era_nusuk.service_vat_percent",
        default=15.0,
        help="VAT percentage applied to the service-fee (margin) line of "
             "agent visa invoices.")

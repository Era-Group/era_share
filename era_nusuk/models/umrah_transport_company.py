# -*- coding: utf-8 -*-

from odoo import models, fields


class UmrahTransportCompany(models.Model):
    _name = "umrah.transport.company"
    _description = "Transport Company"
    _rec_name = "partner_id"
    _order = "id desc"

    # =====================================================
    # Contact (Company)
    # =====================================================

    partner_id = fields.Many2one(
        "res.partner",
        string="Company",
        domain=[("is_company", "=", True)],
        required=True
    )

    # =====================================================
    # Company Info (Readonly from partner)
    # =====================================================

    name = fields.Char(
        related="partner_id.name",
        string="Company Name",
        readonly=True
    )

    mobile = fields.Char(
        related="partner_id.mobile",
        string="Mobile",
        readonly=True
    )

    phone = fields.Char(
        related="partner_id.phone",
        string="Phone",
        readonly=True
    )

    email = fields.Char(
        related="partner_id.email",
        string="Email",
        readonly=True
    )

    website = fields.Char(
        related="partner_id.website",
        string="Website",
        readonly=True
    )

    tax_number = fields.Char(
        related="partner_id.vat",
        string="Tax Number",
        readonly=True
    )

    # =====================================================
    # Location Info (Readonly from partner)
    # =====================================================

    country_id = fields.Many2one(
        "res.country",
        related="partner_id.country_id",
        string="Country",
        readonly=True
    )

    state_id = fields.Many2one(
        "res.country.state",
        related="partner_id.state_id",
        string="City",
        readonly=True
    )

    district = fields.Char(
        related="partner_id.city",
        string="District",
        readonly=True
    )

    street = fields.Char(
        related="partner_id.street",
        string="Street",
        readonly=True
    )

    zip = fields.Char(
        related="partner_id.zip",
        string="ZIP",
        readonly=True
    )

    # =====================================================
    # License Info
    # =====================================================

    license_number = fields.Char(
        string="License Number"
    )

    license_expiry = fields.Date(
        string="License Expiry"
    )

    license_attachment_ids = fields.Many2many(
        "ir.attachment",
        string="License Attachments"
    )

    # =====================================================
    # Performance
    # =====================================================

    rating = fields.Float(
        string="Rating"
    )

    reliability_score = fields.Selection(
        [
            ("excellent", "Excellent"),
            ("good", "Good"),
            ("poor", "Poor"),
        ],
        string="Reliability Score"
    )

    # =====================================================
    # Vehicles
    # =====================================================

    vehicle_ids = fields.One2many(
        "umrah.transport.vehicle",
        "company_id",
        string="Vehicles"
    )
# -*- coding: utf-8 -*-

from odoo import models, fields


class UmrahLocation(models.Model):
    _name = "umrah.location"
    _description = "Umrah Location"
    _rec_name = "name"
    _order = "id desc"

    # =====================================================
    # General Info
    # =====================================================

    name = fields.Char(
        string="Location Name",
        required=True
    )

    code = fields.Char(
        string="Code"
    )

    location_type_id = fields.Many2one(
        "umrah.location.type",
        string="Location Type",
    )

    description = fields.Text(
        string="Description"
    )

    location_environment = fields.Selection(
        [
            ("indoor", "Indoor"),
            ("outdoor", "Outdoor"),
            ("mixed", "Mixed"),
        ],
        string="Environment",
    )

    # =====================================================
    # Address Info
    # =====================================================

    google_map_link = fields.Char(
        string="Google Map Link"
    )

    country_id = fields.Many2one(
        "res.country",
        string="Country"
    )

    state_id = fields.Many2one(
        "res.country.state",
        string="City / State",
        domain="[('country_id', '=', country_id)]"
    )

    district = fields.Char(
        string="District"
    )

    latitude = fields.Float(
        string="Latitude",
        digits=(10, 6)
    )

    longitude = fields.Float(
        string="Longitude",
        digits=(10, 6)
    )

    # =====================================================
    # Other Info
    # =====================================================

    parking_available = fields.Boolean(
        string="Parking Available"
    )

    wheelchair_access = fields.Boolean(
        string="Wheelchair Access"
    )

    capacity = fields.Integer(
        string="Capacity"
    )

    average_visit_duration = fields.Float(
        string="Average Visit Duration (Hours)"
    )

    # =====================================================
    # Contact Info
    # =====================================================

    contact_partner_id = fields.Many2one(
        "res.partner",
        string="Contact Person",
        domain=[("is_company", "=", False)]
    )

    contact_phone = fields.Char(
        related="contact_partner_id.phone",
        string="Phone",
        readonly=True
    )

    contact_email = fields.Char(
        related="contact_partner_id.email",
        string="Email",
        readonly=True
    )
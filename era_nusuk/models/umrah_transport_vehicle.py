# -*- coding: utf-8 -*-

from odoo import models, fields


class UmrahTransportVehicle(models.Model):
    _name = "umrah.transport.vehicle"
    _description = "Transport Vehicle"
    _inherit = ["mail.thread"]
    _rec_name = "name"
    _order = "id desc"

    # =====================================================
    # Status
    # =====================================================

    status = fields.Selection(
        [
            ("available", "Available"),
            ("maintenance", "Maintenance"),
            ("out_of_service", "Out Of Service"),
            ("reserved", "Reserved"),
        ],
        string="Status",
        default="available",
        tracking=True
    )

    # =====================================================
    # Basic Info
    # =====================================================

    name = fields.Char(
        string="Vehicle Name",
        required=True
    )

    vehicle_code = fields.Char(
        string="Vehicle Code"
    )

    vehicle_type = fields.Selection(
        [
            ("bus", "Bus"),
            ("van", "Van"),
            ("car", "Car"),
        ],
        string="Vehicle Type"
    )

    degree = fields.Selection(
        [
            ("vip", "VIP"),
            ("economy", "Economy"),
        ],
        string="Degree"
    )

    # =====================================================
    # Specifications
    # =====================================================

    brand = fields.Char(
        string="Brand"
    )

    model = fields.Char(
        string="Model"
    )

    year = fields.Integer(
        string="Year"
    )

    plate_number = fields.Char(
        string="Plate Number"
    )

    color = fields.Char(
        string="Color"
    )

    gps_enabled = fields.Boolean(
        string="GPS Enabled"
    )

    # =====================================================
    # Capacity
    # =====================================================

    passenger_capacity = fields.Integer(
        string="Passenger Capacity"
    )

    luggage_capacity = fields.Integer(
        string="Luggage Capacity"
    )

    # =====================================================
    # Features
    # =====================================================

    has_ac = fields.Boolean(
        string="AC"
    )

    has_wifi = fields.Boolean(
        string="WiFi"
    )

    has_microphone = fields.Boolean(
        string="Microphone"
    )

    has_tv = fields.Boolean(
        string="TV"
    )

    wheelchair_accessible = fields.Boolean(
        string="Wheelchair Accessible"
    )

    # =====================================================
    # Driver
    # =====================================================

    driver_id = fields.Many2one(
        "umrah.transport.driver",
        string="Driver"
    )

    # =====================================================
    # Notes
    # =====================================================

    note = fields.Text(
        string="Notes"
    )
    company_id = fields.Many2one(
        "umrah.transport.company",
        string="Transport Company",
        ondelete="cascade"
    )
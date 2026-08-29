# -*- coding: utf-8 -*-

from odoo import models, fields


class UmrahTransportDriver(models.Model):
    _name = "umrah.transport.driver"
    _description = "Transport Driver"
    _rec_name = "driver_name"
    _order = "id desc"

    driver_name = fields.Char(
        string="Driver Name",
        required=True
    )

    image = fields.Image(
        string="Driver Image"
    )

    age = fields.Integer(
        string="Age"
    )

    id_number = fields.Char(
        string="ID Number"
    )

    driver_phone = fields.Char(
        string="Phone"
    )

    driver_license = fields.Char(
        string="License Number"
    )

    license_attachment_ids = fields.Many2many(
        "ir.attachment",
        string="Driver Attachments"
    )
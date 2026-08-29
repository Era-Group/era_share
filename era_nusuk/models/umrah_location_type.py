# -*- coding: utf-8 -*-

from odoo import models, fields


class UmrahLocationType(models.Model):
    _name = "umrah.location.type"
    _description = "Umrah Location Type"
    _rec_name = "name"
    _order = "id desc"

    # ==========================================
    # Basic Information
    # ==========================================

    name = fields.Char(
        string="Name",
        required=True,
    )

    code = fields.Char(
        string="Code",
    )

    description = fields.Text(
        string="Description",
    )
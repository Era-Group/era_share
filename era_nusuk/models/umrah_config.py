# -*- coding: utf-8 -*-
"""Configurable master data.

These lookups replace hardcoded selections so the business can adjust them
from Configuration menus without code changes:
- umrah.occupancy.type : room-sharing levels (double/triple/quad/...)
- umrah.agent.tier     : agent pricing tiers
- umrah.service        : catalog of services a package can include
"""

from odoo import models, fields, api


class UmrahOccupancyType(models.Model):
    _name = "umrah.occupancy.type"
    _description = "Room Occupancy Type"
    _order = "capacity, id"

    name = fields.Char(string="Name", required=True, translate=True)
    code = fields.Char(string="Code", required=True)
    capacity = fields.Integer(
        string="Persons per Room", required=True, default=2)
    active = fields.Boolean(default=True)

    _sql_constraints = [
        ("code_uniq", "unique(code)", "Occupancy code must be unique."),
        ("capacity_positive", "CHECK(capacity > 0)",
         "Capacity must be positive."),
    ]


class UmrahAgentTier(models.Model):
    _name = "umrah.agent.tier"
    _description = "Agent Pricing Tier"
    _order = "sequence, id"

    name = fields.Char(string="Name", required=True, translate=True)
    code = fields.Char(string="Code", required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)

    _sql_constraints = [
        ("code_uniq", "unique(code)", "Tier code must be unique."),
    ]


class UmrahService(models.Model):
    _name = "umrah.service"
    _description = "Umrah Service"
    _order = "sequence, id"

    name = fields.Char(string="Name", required=True, translate=True)
    code = fields.Char(string="Code", required=True)
    sequence = fields.Integer(default=10)
    description = fields.Text(string="Description")
    active = fields.Boolean(default=True)

    _sql_constraints = [
        ("code_uniq", "unique(code)", "Service code must be unique."),
    ]

# -*- coding: utf-8 -*-

from odoo import models, fields, api, _


class UmrahMovement(models.Model):
    """Unified transport movement request, mirroring the Nusuk Masar request
    types (arrival / intercity / departure) plus local-only types."""
    _name = "umrah.movement"
    _description = "Transport Movement"
    _inherit = ["mail.thread"]
    _rec_name = "name"
    _order = "id desc"

    name = fields.Char(
        string="Reference",
        required=True,
        copy=False,
        default=lambda self: _("New"),
    )

    movement_type = fields.Selection(
        [
            ("arrival", "Arrival"),
            ("intercity", "Intercity"),
            ("departure", "Departure"),
            ("ziyarah", "Ziyarah"),
            ("other", "Other"),
        ],
        string="Movement Type",
        required=True,
        default="arrival",
        tracking=True,
    )

    # Nusuk Masar transport request reference (per-request approval id).
    masar_request_ref = fields.Char(string="Masar Request Ref", copy=False)

    flight_number = fields.Char(string="Flight Number")

    # Informational only - this screen never bills (no partner / no invoicing).
    agent_id = fields.Many2one(
        "umrah.agent",
        string="Agent",
        readonly=True,
    )

    description = fields.Text(string="Description")

    max_capacity = fields.Integer(string="Max Capacity")

    date_from = fields.Date(string="Date From")
    date_to = fields.Date(string="Date To")

    transport_company_id = fields.Many2one(
        "umrah.transport.company",
        string="Transport Company",
    )

    vehicle_line_ids = fields.One2many(
        "umrah.movement.vehicle.line",
        "movement_id",
        string="Vehicles",
    )

    route_ids = fields.One2many(
        "umrah.movement.route",
        "movement_id",
        string="Routes",
    )

    group_ids = fields.Many2many(
        "umrah.group",
        string="Groups",
    )

    pilgrim_ids = fields.Many2many(
        "umrah.pilgrim",
        compute="_compute_pilgrims",
        string="Pilgrims",
    )

    member_ids = fields.Many2many(
        "umrah.group.member",
        compute="_compute_pilgrims",
        string="Members",
    )

    group_count = fields.Integer(string="Groups", compute="_compute_counts")
    pilgrim_count = fields.Integer(string="Pilgrims", compute="_compute_counts")

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("name") or vals["name"] == _("New"):
                vals["name"] = self.env["ir.sequence"].next_by_code(
                    "umrah.movement"
                ) or _("New")
        return super().create(vals_list)

    @api.depends("group_ids.member_ids")
    def _compute_pilgrims(self):
        for rec in self:
            members = rec.group_ids.mapped("member_ids")
            rec.member_ids = members
            rec.pilgrim_ids = members.mapped("pilgrim_id")

    @api.depends("group_ids", "pilgrim_ids")
    def _compute_counts(self):
        for rec in self:
            rec.group_count = len(rec.group_ids)
            rec.pilgrim_count = len(rec.pilgrim_ids)

    def action_view_groups(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Groups"),
            "res_model": "umrah.group",
            "view_mode": "list,form",
            "domain": [("id", "in", self.group_ids.ids)],
            "context": {"create": False},
        }

    def action_view_pilgrims(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Pilgrims"),
            "res_model": "umrah.pilgrim",
            "view_mode": "list,form",
            "domain": [("id", "in", self.pilgrim_ids.ids)],
            "context": {"create": False},
        }


class UmrahMovementRoute(models.Model):
    _name = "umrah.movement.route"
    _description = "Movement Route"

    movement_id = fields.Many2one(
        "umrah.movement",
        string="Movement",
        ondelete="cascade",
    )

    from_location_id = fields.Many2one("umrah.location", string="From Location")
    from_date = fields.Datetime(string="Departure Time")
    to_location_id = fields.Many2one("umrah.location", string="To Location")
    to_date = fields.Datetime(string="Arrival Time")


class UmrahMovementVehicleLine(models.Model):
    _name = "umrah.movement.vehicle.line"
    _description = "Movement Vehicle"

    movement_id = fields.Many2one(
        "umrah.movement",
        string="Movement",
        ondelete="cascade",
    )

    vehicle_id = fields.Many2one(
        "umrah.transport.vehicle",
        string="Vehicle",
        required=True,
    )

    passenger_capacity = fields.Integer(
        related="vehicle_id.passenger_capacity",
        string="Passenger Capacity",
        readonly=True,
    )

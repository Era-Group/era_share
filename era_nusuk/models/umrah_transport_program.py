# -*- coding: utf-8 -*-

from odoo import models, fields, api, _
from odoo.exceptions import UserError


class UmrahTransportProgram(models.Model):
    _name = "umrah.transport.program"
    _description = "Transport Program"
    _rec_name = "name"
    _order = "id desc"

    # =====================================================
    # Basic Info
    # =====================================================

    name = fields.Char(
        string="Program Name",
        required=True
    )

    description = fields.Text(
        string="Description"
    )

    max_capacity = fields.Integer(
        string="Max Capacity"
    )

    date_from = fields.Date(
        string="Date From"
    )

    date_to = fields.Date(
        string="Date To"
    )

    # =====================================================
    # Transport Company
    # =====================================================

    transport_company_id = fields.Many2one(
        "umrah.transport.company",
        string="Transport Company",
        required=True
    )

    # =====================================================
    # Vehicles
    # =====================================================

    vehicle_line_ids = fields.One2many(
        "umrah.transport.program.vehicle.line",
        "program_id",
        string="Vehicles"
    )

    # =====================================================
    # Routes
    # =====================================================

    route_ids = fields.One2many(
        "umrah.transport.program.route",
        "program_id",
        string="Routes"
    )

    # =====================================================
    # Groups
    # =====================================================

    group_ids = fields.Many2many(
        "umrah.group",
        string="Groups"
    )

    # =====================================================
    # Billing partner source (prerequisite for invoicing)
    # =====================================================

    agent_id = fields.Many2one(
        "umrah.agent",
        string="Agent",
    )

    partner_id = fields.Many2one(
        "res.partner",
        string="Customer",
        related="agent_id.partner_id",
        store=True,
        readonly=True,
    )

    # =====================================================
    # Amount / Invoicing (Section 2.1)
    # =====================================================

    currency_id = fields.Many2one(
        "res.currency",
        string="Currency",
        default=lambda self: self.env.company.currency_id,
    )

    amount = fields.Monetary(
        string="Amount",
        currency_field="currency_id",
    )

    invoice_id = fields.Many2one(
        "account.move",
        string="Invoice",
        readonly=True,
    )

    pilgrim_ids = fields.Many2many(
        "umrah.pilgrim",
        compute="_compute_pilgrims",
        string="Pilgrims"
    )

    member_ids = fields.Many2many(
        "umrah.group.member",
        compute="_compute_pilgrims",
        string="Members"
    )
    group_count = fields.Integer(
        string="Groups",
        compute="_compute_counts"
    )

    pilgrim_count = fields.Integer(
        string="Pilgrims",
        compute="_compute_counts"
    )
    route_timeline_html = fields.Html(
        string="Route Timeline",
        compute="_compute_route_timeline_html",
        sanitize=False
    )

    @api.depends(
        "route_ids",
        "route_ids.from_location_id",
        "route_ids.to_location_id",
        "route_ids.from_date",
        "route_ids.to_date"
    )
    def _compute_route_timeline_html(self):

        for program in self:

            if not program.route_ids:
                program.route_timeline_html = """
                    <div style="padding:20px;color:#888;">
                        No routes defined for this program
                    </div>
                """
                continue

            routes = program.route_ids.sorted(
                key=lambda r: r.from_date or fields.Datetime.now()
            )

            html = """
            <div style="
                background:var(--o-view-background-color, #ffffff);
                border-radius:12px;
                padding:25px;
                border:1px solid #d1d5db;
            ">

            <h3 style="
                margin-bottom:20px;
                color:#2563eb;
                font-weight:600;
            ">
            <i class="fa fa-road"></i> Program Route Timeline
            </h3>

            <div style="
                border-left:3px solid #2563eb;
                padding-left:25px;
                margin-left:10px;
            ">
            """

            for route in routes:
                from_loc = route.from_location_id.name or ""
                to_loc = route.to_location_id.name or ""

                from_time = route.from_date.strftime("%Y-%m-%d %H:%M") if route.from_date else ""
                to_time = route.to_date.strftime("%Y-%m-%d %H:%M") if route.to_date else ""

                html += f"""

                <div style="
                    position:relative;
                    margin-bottom:32px;
                ">

                    <div style="
                        position:absolute;
                        left:-34px;
                        top:6px;
                        width:14px;
                        height:14px;
                        background:#2563eb;
                        border-radius:50%;
                        box-shadow:0 0 0 3px rgba(37,99,235,0.15);
                    "></div>

                    <div style="
                        font-size:16px;
                        font-weight:700;
                        color:#111827;
                    ">
                        {from_loc}
                    </div>

                    <div style="
                        font-size:13px;
                        color:#374151;
                        margin-bottom:6px;
                    ">
                        {from_time}
                    </div>

                    <div style="
                        color:#2563eb;
                        margin:6px 0;
                        font-size:14px;
                    ">
                        <i class="fa fa-long-arrow-down"></i>
                    </div>

                    <div style="
                        font-size:16px;
                        font-weight:700;
                        color:#111827;
                    ">
                        {to_loc}
                    </div>

                    <div style="
                        font-size:13px;
                        color:#374151;
                    ">
                        {to_time}
                    </div>

                </div>

                """

            html += """
            </div>
            </div>
            """

            program.route_timeline_html = html

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

    # =====================================================
    # Smart Button Actions
    # =====================================================

    def action_view_groups(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Groups",
            "res_model": "umrah.group",
            "view_mode": "list,form",
            "domain": [("id", "in", self.group_ids.ids)],
            "context": {"create": False},
        }

    def action_view_pilgrims(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Pilgrims",
            "res_model": "umrah.pilgrim",
            "view_mode": "list,form",
            "domain": [("id", "in", self.pilgrim_ids.ids)],
            "context": {"create": False},
        }

    # =====================================================
    # Invoice generation (Section 2.1)
    # =====================================================

    def action_create_transport_invoice(self):
        self.ensure_one()

        if not self.partner_id:
            raise UserError(_(
                "Set an Agent (with a linked partner) before creating the invoice."
            ))

        move = self.env["account.move"].create({
            "move_type": "out_invoice",
            "partner_id": self.partner_id.id,
            "umrah_transport_program_id": self.id,
            "invoice_line_ids": [(0, 0, {
                "name": _("Transport Program: %s") % self.display_name,
                "quantity": 1,
                "price_unit": self.amount,
            })],
        })
        self.invoice_id = move.id

        return {
            "type": "ir.actions.act_window",
            "name": _("Invoice"),
            "res_model": "account.move",
            "res_id": move.id,
            "view_mode": "form",
        }





class UmrahTransportProgramRoute(models.Model):
    _name = "umrah.transport.program.route"
    _description = "Transport Program Route"

    program_id = fields.Many2one(
        "umrah.transport.program",
        string="Program",
        ondelete="cascade"
    )

    from_location_id = fields.Many2one(
        "umrah.location",
        string="From Location"
    )

    from_date = fields.Datetime(
        string="Departure Time"
    )

    to_location_id = fields.Many2one(
        "umrah.location",
        string="To Location"
    )

    to_date = fields.Datetime(
        string="Arrival Time"
    )




class UmrahTransportProgramVehicleLine(models.Model):
    _name = "umrah.transport.program.vehicle.line"
    _description = "Program Vehicle"

    program_id = fields.Many2one(
        "umrah.transport.program",
        string="Program",
        ondelete="cascade"
    )

    vehicle_id = fields.Many2one(
        "umrah.transport.vehicle",
        string="Vehicle",
        required=True
    )

    passenger_capacity = fields.Integer(
        related="vehicle_id.passenger_capacity",
        string="Passenger Capacity",
        readonly=True
    )
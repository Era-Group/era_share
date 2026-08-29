# -*- coding: utf-8 -*-

from odoo import models, fields, api
from odoo.exceptions import ValidationError
from odoo import _

class UmrahCustomerContract(models.Model):
    _name = "umrah.customer.contract"
    _description = "Umrah Customer Contract"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _rec_name = "name"

    # =========================
    # BASIC INFO
    # =========================

    name = fields.Char(
        string="Contract Number",
        required=True,
        tracking=True
    )

    customer_id = fields.Many2one(
        "umrah.customer",
        string="Customer",
        required=True,
        tracking=True
    )
    analytic_plan_id = fields.Many2one(
        "account.analytic.plan",
        string="Analytic Plan",
        tracking=True
    )

    analytic_account_id = fields.Many2one(
        "account.analytic.account",
        string="Analytic Account",
        domain="[('plan_id','=',analytic_plan_id)]",
        tracking=True
    )

    description = fields.Text(
        string="Description"
    )

    # =========================
    # DATES
    # =========================

    date_from = fields.Date(
        string="From Date",
        required=True,
        tracking=True
    )

    date_to = fields.Date(
        string="To Date",
        required=True,
        tracking=True
    )

    # =========================
    # STATE
    # =========================

    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("submit", "Submitted"),
            ("waiting", "Waiting Approval"),
            ("approved", "Approved"),
            ("canceled", "Canceled"),
        ],
        string="Status",
        default="draft",
        tracking=True
    )

    # =========================
    # GROUPS
    # =========================

    group_ids = fields.One2many(
        "umrah.group",
        "contract_id",
        string="Groups"
    )

    # =========================
    # TRIPS (Computed)
    # =========================

    trip_ids = fields.Many2many(
        "umrah.trip",
        string="Trips",
        compute="_compute_trips",
        store=True
    )

    # =========================
    # PILGRIMS COUNT
    # =========================

    pilgrim_count = fields.Integer(
        string="Total Pilgrims",
        compute="_compute_pilgrim_count",
        store=True
    )
    group_count = fields.Integer(
        compute="_compute_counts",
        string="Groups"
    )

    trip_count = fields.Integer(
        compute="_compute_counts",
        string="Trips"
    )

    program_count = fields.Integer(
        compute="_compute_counts",
        string="Programs"
    )

    booking_count = fields.Integer(
        compute="_compute_counts",
        string="Bookings"
    )

    invoice_count = fields.Integer(
        string="Invoices",
        compute="_compute_invoice_count"
    )

    def _compute_invoice_count(self):
        for rec in self:
            group_ids = rec.group_ids.ids

            rec.invoice_count = self.env["account.move"].search_count([
                ("move_type", "=", "out_invoice"),
                "|",
                ("umrah_contract_id", "=", rec.id),
                ("umrah_group_id", "in", group_ids)
            ])

    def _compute_counts(self):

        Program = self.env["umrah.transport.program"]
        Booking = self.env["umrah.hotel.booking"]

        for rec in self:
            groups = rec.group_ids
            trips = groups.mapped("trip_ids")
            pilgrims = groups.mapped("member_ids.pilgrim_id")

            programs = Program.search([
                ("group_ids", "in", groups.ids)
            ])

            bookings = Booking.search([
                ("group_ids", "in", groups.ids)
            ])

            rec.group_count = len(groups)
            rec.trip_count = len(trips)
            rec.program_count = len(programs)
            rec.booking_count = len(bookings)

    # =========================
    # COMPUTE TRIPS
    # =========================

    @api.depends("group_ids.trip_ids")
    def _compute_trips(self):
        for contract in self:
            trips = contract.group_ids.mapped("trip_ids")
            contract.trip_ids = trips

    # =========================
    # COMPUTE PILGRIMS
    # =========================

    @api.depends("group_ids.member_ids.pilgrim_id")
    def _compute_pilgrim_count(self):
        for contract in self:
            pilgrims = contract.group_ids.mapped("member_ids.pilgrim_id")
            contract.pilgrim_count = len(pilgrims)

    # =========================
    # ACTIONS
    # =========================

    def action_submit(self):
        for rec in self:
            rec.state = "submit"

    def action_waiting(self):
        for rec in self:
            rec.state = "waiting"

    def action_approve(self):
        for rec in self:
            rec.state = "approved"

    def action_cancel(self):
        for rec in self:
            rec.state = "canceled"

    def action_reset_to_draft(self):
        for rec in self:
            rec.state = "draft"

    def action_view_groups(self):
        self.ensure_one()

        return {
            "type": "ir.actions.act_window",
            "name": "Groups",
            "res_model": "umrah.group",
            "view_mode": "list,form",
            "domain": [("id", "in", self.group_ids.ids)],
        }

    def action_view_trips(self):
        self.ensure_one()

        return {
            "type": "ir.actions.act_window",
            "name": "Trips",
            "res_model": "umrah.trip",
            "view_mode": "list,form",
            "domain": [("id", "in", self.trip_ids.ids)],
        }

    def action_view_pilgrims(self):
        self.ensure_one()

        pilgrim_ids = self.group_ids.mapped("member_ids.pilgrim_id").ids

        return {
            "type": "ir.actions.act_window",
            "name": "Pilgrims",
            "res_model": "umrah.pilgrim",
            "view_mode": "list,form",
            "domain": [("id", "in", pilgrim_ids)],
        }

    def action_view_programs(self):
        self.ensure_one()

        return {
            "type": "ir.actions.act_window",
            "name": "Programs",
            "res_model": "umrah.transport.program",
            "view_mode": "list,form",
            "domain": [("group_ids", "in", self.group_ids.ids)],
        }

    def action_view_bookings(self):
        self.ensure_one()

        return {
            "type": "ir.actions.act_window",
            "name": "Hotel Bookings",
            "res_model": "umrah.hotel.booking",
            "view_mode": "list,form",
            "domain": [("group_ids", "in", self.group_ids.ids)],
        }

    def action_view_invoices(self):
        self.ensure_one()

        group_ids = self.group_ids.ids

        return {
            "type": "ir.actions.act_window",
            "name": "Invoices",
            "res_model": "account.move",
            "view_mode": "list,form",
            "domain": [
                ("move_type", "=", "out_invoice"),
                "|",
                ("umrah_contract_id", "=", self.id),
                ("umrah_group_id", "in", group_ids)
            ],
        }

    def action_create_invoice(self):
        self.ensure_one()

        if not self.analytic_plan_id or not self.analytic_account_id:
            raise ValidationError(
                _("You must set Analytic Plan and Analytic Account before creating the invoice.")
            )

        partner = self.customer_id.company_partner_id
        analytic_account = self.analytic_account_id

        invoice_vals = {
            "move_type": "out_invoice",
            "partner_id": partner.id,
            "umrah_contract_id": self.id,
            "umrah_customer_id": self.customer_id.id,
            "invoice_line_ids": [(0, 0, {
                "name": f"Umrah Contract {self.name}",
                "quantity": 1,
                "price_unit": 0,
                "analytic_distribution": {
                    analytic_account.id: 100
                },
            })],
        }

        invoice = self.env["account.move"].create(invoice_vals)

        return {
            "type": "ir.actions.act_window",
            "name": "Invoice",
            "res_model": "account.move",
            "res_id": invoice.id,
            "view_mode": "form",
        }
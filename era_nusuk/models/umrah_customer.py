# -*- coding: utf-8 -*-

from odoo import models, fields, api, _
from odoo.exceptions import ValidationError


class UmrahCustomer(models.Model):
    _name = "umrah.customer"
    _description = "Umrah Customer"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _rec_name = "company_partner_id"
    _order = "id desc"

    # =====================================================
    # Status
    # =====================================================

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
        tracking=True,
    )

    # =====================================================
    # Company (Main Partner)
    # =====================================================

    company_partner_id = fields.Many2one(
        "res.partner",
        string="Company Name",
        required=True,
        domain=[("is_company", "=", True)],
        tracking=True,
    )

    # =====================================================
    # General Information (Related from Partner)
    # =====================================================

    vat = fields.Char(
        string="Tax Number",
        related="company_partner_id.vat",
        store=True,
        readonly=False,
    )

    commercial_register = fields.Char(
        string="Commercial Register",
        related="company_partner_id.ref",
        store=True,
        readonly=False,
    )

    mobile = fields.Char(
        string="Mobile",
        related="company_partner_id.mobile",
        store=True,
        readonly=False,
    )

    phone = fields.Char(
        string="Phone",
        related="company_partner_id.phone",
        store=True,
        readonly=False,
    )

    email = fields.Char(
        string="Email",
        related="company_partner_id.email",
        store=True,
        readonly=False,
    )

    # =====================================================
    # Address Information
    # =====================================================

    country_id = fields.Many2one(
        "res.country",
        string="Country",
        related="company_partner_id.country_id",
        store=True,
        readonly=False,
    )

    city = fields.Char(
        string="City",
        related="company_partner_id.city",
        store=True,
        readonly=False,
    )

    district = fields.Char(
        string="District",
        related="company_partner_id.street2",
        store=True,
        readonly=False,
    )

    state_id = fields.Many2one(
        "res.country.state",
        string="State / Emirate",
        related="company_partner_id.state_id",
        store=True,
        readonly=False,
    )

    zip = fields.Char(
        string="Postal Code",
        related="company_partner_id.zip",
        store=True,
        readonly=False,
    )

    street = fields.Char(
        string="Street",
        related="company_partner_id.street",
        store=True,
        readonly=False,
    )

    # =====================================================
    # Responsible Person
    # =====================================================

    responsible_id = fields.Many2one(
        "res.partner",
        string="Responsible Person",
        domain="[('is_company','=',False)]",
    )

    responsible_email = fields.Char(
        string="Responsible Email",
        related="responsible_id.email",
        store=True,
        readonly=False,
    )

    responsible_mobile = fields.Char(
        string="Responsible Mobile",
        related="responsible_id.mobile",
        store=True,
        readonly=False,
    )

    # =====================================================
    # Sales Person
    # =====================================================

    salesperson_id = fields.Many2one(
        "res.users",
        string="Salesperson",
        related="company_partner_id.user_id",
        store=True,
        readonly=False,
    )

    # =====================================================
    # Financial Information
    # =====================================================

    analytic_plan_id = fields.Many2one(
        "account.analytic.plan",
        string="Analytic Plan",
    )

    analytic_account_ids = fields.Many2many(
        "account.analytic.account",
        string="Analytic Accounts",
        domain="[('plan_id','=',analytic_plan_id)]",
    )

    # =====================================================
    # Contracts / Groups / Trips
    # =====================================================

    contract_ids = fields.One2many(
        "umrah.customer.contract",
        "customer_id",
        string="Contracts",
    )

    contract_count = fields.Integer(
        string="Contracts Count",
        compute="_compute_contract_count",
    )

    group_count = fields.Integer(
        string="Groups Count",
        compute="_compute_group_trip_counts",
    )

    trip_count = fields.Integer(
        string="Trips Count",
        compute="_compute_group_trip_counts",
    )
    invoice_count = fields.Integer(
        string="Invoices",
        compute="_compute_invoice_count"
    )

    def _compute_invoice_count(self):
        Move = self.env["account.move"]

        for rec in self:
            contract_ids = rec.contract_ids.ids
            group_ids = rec.contract_ids.mapped("group_ids").ids

            rec.invoice_count = Move.search_count([
                ("move_type", "=", "out_invoice"),
                "|",
                ("umrah_contract_id", "in", contract_ids),
                ("umrah_group_id", "in", group_ids),
            ])

    def _compute_contract_count(self):
        for rec in self:
            rec.contract_count = len(rec.contract_ids)

    @api.depends(
        "contract_ids",
        "contract_ids.group_ids",
        "contract_ids.group_ids.trip_ids",
    )
    def _compute_group_trip_counts(self):
        for rec in self:
            groups = rec.contract_ids.mapped("group_ids")
            trips = groups.mapped("trip_ids")
            rec.group_count = len(groups)
            rec.trip_count = len(trips)

    # =====================================================
    # Constraints
    # =====================================================

    _sql_constraints = [
        (
            "unique_company_partner",
            "unique(company_partner_id)",
            "This company is already registered as Umrah Customer.",
        )
    ]

    # =====================================================
    # Buttons / Actions (Workflow)
    # =====================================================

    def action_submit(self):
        for rec in self:
            if rec.state != "draft":
                raise ValidationError(_("Only Draft records can be submitted."))
            rec.state = "submit"

    def action_waiting(self):
        for rec in self:
            if rec.state != "submit":
                raise ValidationError(_("Record must be Submitted first."))
            rec.state = "waiting"

    def action_approve(self):
        for rec in self:
            if rec.state != "waiting":
                raise ValidationError(_("Record must be Waiting Approval first."))
            rec.state = "approved"

    def action_cancel(self):
        for rec in self:
            rec.state = "canceled"

    def action_reset_to_draft(self):
        for rec in self:
            rec.state = "draft"

    # =====================================================
    # Smart Buttons Actions
    # =====================================================

    def action_view_contracts(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Contracts",
            "res_model": "umrah.customer.contract",
            "view_mode": "list,form",
            "domain": [("customer_id", "=", self.id)],
            "context": {"default_customer_id": self.id},
        }

    def action_view_groups(self):
        self.ensure_one()
        group_ids = self.contract_ids.mapped("group_ids").ids
        return {
            "type": "ir.actions.act_window",
            "name": "Groups",
            "res_model": "umrah.group",
            "view_mode": "list,form",
            "domain": [("id", "in", group_ids)],
        }

    def action_view_trips(self):
        self.ensure_one()
        trip_ids = self.contract_ids.mapped("group_ids.trip_ids").ids
        return {
            "type": "ir.actions.act_window",
            "name": "Trips",
            "res_model": "umrah.trip",
            "view_mode": "list,form",
            "domain": [("id", "in", trip_ids)],
        }

    def action_view_invoices(self):
        self.ensure_one()

        contract_ids = self.contract_ids.ids
        group_ids = self.contract_ids.mapped("group_ids").ids

        return {
            "type": "ir.actions.act_window",
            "name": "Invoices",
            "res_model": "account.move",
            "view_mode": "list,form",
            "domain": [
                ("move_type", "=", "out_invoice"),
                "|",
                ("umrah_contract_id", "in", contract_ids),
                ("umrah_group_id", "in", group_ids),
            ],
        }
# -*- coding: utf-8 -*-

from odoo import models, fields, api, _
from odoo.exceptions import UserError


class UmrahPackage(models.Model):
    """Umrah program/package: nights, hotels, included services and
    per-occupancy / per-tier pricing. Mirrors a Nusuk Masar program."""
    _name = "umrah.package"
    _description = "Umrah Package"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "id desc"

    name = fields.Char(string="Package Name", required=True, tracking=True)
    code = fields.Char(
        string="Code", required=True, copy=False,
        default=lambda self: _("New"))
    active = fields.Boolean(default=True)
    description = fields.Text(string="Description")

    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("approved", "Approved"),
            ("archived", "Archived"),
        ],
        string="Status", default="draft", tracking=True)

    # Nusuk Masar program reference (packages are registered on Masar).
    masar_program_ref = fields.Char(string="Masar Program Ref", copy=False)

    # Itinerary
    nights_makkah = fields.Integer(string="Nights in Makkah", default=0)
    nights_madinah = fields.Integer(string="Nights in Madinah", default=0)
    total_nights = fields.Integer(
        string="Total Nights", compute="_compute_total_nights", store=True)
    makkah_hotel_id = fields.Many2one(
        "umrah.hotel", string="Makkah Hotel",
        domain=[("city", "=", "mecca")])
    madinah_hotel_id = fields.Many2one(
        "umrah.hotel", string="Madinah Hotel",
        domain=[("city", "=", "medina")])

    # Included services (configurable catalog, not hardcoded booleans)
    service_ids = fields.Many2many(
        "umrah.service", string="Included Services")

    # Pricing
    currency_id = fields.Many2one(
        "res.currency", string="Currency",
        default=lambda self: self.env.company.currency_id)
    price_line_ids = fields.One2many(
        "umrah.package.price", "package_id", string="Price Lines")

    group_ids = fields.One2many(
        "umrah.group", "package_id", string="Groups")
    group_count = fields.Integer(compute="_compute_group_count",
                                 string="Groups")

    @api.depends("nights_makkah", "nights_madinah")
    def _compute_total_nights(self):
        for rec in self:
            rec.total_nights = (rec.nights_makkah or 0) + (rec.nights_madinah or 0)

    def _compute_group_count(self):
        for rec in self:
            rec.group_count = len(rec.group_ids)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("code") or vals["code"] == _("New"):
                vals["code"] = self.env["ir.sequence"].next_by_code(
                    "umrah.package") or _("New")
        return super().create(vals_list)

    def action_approve(self):
        self.write({"state": "approved"})

    def action_archive_package(self):
        self.write({"state": "archived", "active": False})

    def action_reset_to_draft(self):
        self.write({"state": "draft", "active": True})

    def action_view_groups(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Groups"),
            "res_model": "umrah.group",
            "view_mode": "list,form",
            "domain": [("package_id", "=", self.id)],
            "context": {"default_package_id": self.id},
        }

    def get_price(self, occupancy_type, tier=None, date=None):
        """Return the (sale_price, cost_price) for an occupancy type,
        optionally narrowed by agent tier and date. Tier-specific lines win
        over generic (no-tier) lines; date-bounded lines win over open ones."""
        self.ensure_one()
        date = date or fields.Date.context_today(self)
        lines = self.price_line_ids.filtered(
            lambda l: l.occupancy_type_id == occupancy_type
            and (not l.date_from or l.date_from <= date)
            and (not l.date_to or l.date_to >= date))
        if tier:
            tier_lines = lines.filtered(lambda l: l.tier_id == tier)
            if tier_lines:
                lines = tier_lines
            else:
                lines = lines.filtered(lambda l: not l.tier_id)
        else:
            lines = lines.filtered(lambda l: not l.tier_id)
        if not lines:
            raise UserError(_(
                "No price configured on package %(pkg)s for occupancy "
                "%(occ)s.", pkg=self.display_name,
                occ=occupancy_type.display_name))
        line = lines.sorted(
            key=lambda l: (l.date_from or fields.Date.to_date("1900-01-01")),
            reverse=True)[0]
        return (line.sale_price, line.cost_price)


class UmrahPackagePrice(models.Model):
    _name = "umrah.package.price"
    _description = "Package Price Line"
    _order = "occupancy_type_id, tier_id, date_from desc"

    package_id = fields.Many2one(
        "umrah.package", string="Package", required=True, ondelete="cascade")
    occupancy_type_id = fields.Many2one(
        "umrah.occupancy.type", string="Occupancy", required=True)
    tier_id = fields.Many2one(
        "umrah.agent.tier", string="Agent Tier",
        help="Leave empty to apply to all tiers.")
    sale_price = fields.Monetary(
        string="Sale Price / Pilgrim", currency_field="currency_id")
    cost_price = fields.Monetary(
        string="Cost / Pilgrim", currency_field="currency_id")
    currency_id = fields.Many2one(
        related="package_id.currency_id", store=True)
    date_from = fields.Date(string="From")
    date_to = fields.Date(string="To")

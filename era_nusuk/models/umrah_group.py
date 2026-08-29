# -*- coding: utf-8 -*-

from odoo import models, fields, api,_
from odoo.exceptions import ValidationError


class UmrahGroup(models.Model):
    _name = 'umrah.group'
    _description = 'Umrah Group'
    _rec_name = 'name'
    _inherit = ['mail.thread','mail.activity.mixin']

    # --------------------------------------------------
    # Basic Information
    # --------------------------------------------------

    name = fields.Char(string='Group Name', required=True)
    code = fields.Char(string='Group Code', required=True)
    description = fields.Text(string='Description')

    # --------------------------------------------------
    # Trip Data (arrival / departure / hotels)
    # --------------------------------------------------

    arrival_flight_no = fields.Char(string='Arrival Flight Number')
    arrival_airport = fields.Char(string='Arrival Airport')
    arrival_date = fields.Date(string='Arrival Date')
    departure_flight_no = fields.Char(string='Departure Flight Number')
    departure_airport = fields.Char(string='Departure Airport')
    departure_date = fields.Date(string='Departure Date')
    makkah_hotel_id = fields.Many2one(
        'umrah.hotel', string='Makkah Hotel',
        domain=[('city', '=', 'mecca')])
    madinah_hotel_id = fields.Many2one(
        'umrah.hotel', string='Madinah Hotel',
        domain=[('city', '=', 'medina')])

    # Nusuk Masar mirror: the agent creates the group on Masar and sends it
    # to the company; groups must be linked to a registered program/package.
    masar_group_number = fields.Char(string='Masar Group No.', copy=False)
    package_id = fields.Many2one(
        'umrah.package', string='Package',
        domain=[('state', '=', 'approved')])

    # --------------------------------------------------
    # Trips
    # --------------------------------------------------

    trip_ids = fields.Many2many(
        'umrah.trip',
        'umrah_group_trip_rel',
        'group_id',
        'trip_id',
        string='Trips'
    )

    trip_count = fields.Integer(
        string="Trips",
        compute="_compute_trip_count"
    )

    # --------------------------------------------------
    # Group Staff
    # --------------------------------------------------

    leader_id = fields.Many2one('umrah.pilgrim', string='Group Leader')
    guide_id = fields.Many2one('res.partner', string='Guide')

    agent_id = fields.Many2one(
        'umrah.agent',
        string='Responsible Agent'
    )

    # --------------------------------------------------
    # Members
    # --------------------------------------------------

    member_ids = fields.One2many(
        'umrah.group.member',
        'group_id',
        string='Members'
    )

    current_members = fields.Integer(
        string='Current Members',
        compute='_compute_current_members',
        store=True
    )

    # --------------------------------------------------
    # Capacity
    # --------------------------------------------------

    max_capacity = fields.Integer(
        string='Maximum Capacity',
        required=True,
        default=50
    )

    available_spots = fields.Integer(
        string='Available Spots',
        compute='_compute_available_spots',
        store=True
    )

    # --------------------------------------------------
    # Dates
    # --------------------------------------------------

    formation_date = fields.Date(
        string='Formation Date',
        default=fields.Date.today
    )

    # --------------------------------------------------
    # Group Type
    # --------------------------------------------------

    group_type = fields.Selection([
        ('family', 'Family Group'),
        ('friends', 'Friends Group'),
        ('organization', 'Organization Group'),
        ('mixed', 'Mixed Group'),
        ('vip', 'VIP Group')
    ], string='Group Type', required=True, default='mixed')

    # --------------------------------------------------
    # Restrictions
    # --------------------------------------------------

    gender_restriction = fields.Selection([
        ('none', 'No Restriction'),
        ('male_only', 'Male Only'),
        ('female_only', 'Female Only'),
        ('families_only', 'Families Only')
    ], string='Gender Restriction', default='none')

    age_restriction = fields.Selection([
        ('none', 'No Restriction'),
        ('adults_only', 'Adults Only (18+)'),
        ('seniors_only', 'Seniors Only (60+)'),
        ('youth_only', 'Youth Only (18-35)')
    ], string='Age Restriction', default='none')

    # --------------------------------------------------
    # Relations
    # --------------------------------------------------

    hotel_booking_ids = fields.Many2many(
        'umrah.hotel.booking',
        'umrah_hotel_booking_group_rel',
        'group_id',
        'booking_id',
        string='Hotel Bookings'
    )

    contract_id = fields.Many2one(
        "umrah.customer.contract",
        string="Contract"
    )

    # --------------------------------------------------
    # Status
    # --------------------------------------------------

    status = fields.Selection([
        ('draft', 'Draft'),
        ('forming', 'Forming'),
        ('confirmed', 'Confirmed'),
        ('departed', 'Departed'),
        ('in_saudi', 'In Saudi Arabia'),
        ('returned', 'Returned'),
        ('completed', 'Completed'),
        ('cancelled', 'Cancelled')
    ], string='Status', default='draft', tracking=True)

    # --------------------------------------------------
    # Special Requirements
    # --------------------------------------------------

    special_requirements = fields.Text(string='Special Requirements')
    dietary_requirements = fields.Text(string='Dietary Requirements')
    medical_requirements = fields.Text(string='Medical Requirements')

    # --------------------------------------------------
    # Communication
    # --------------------------------------------------

    whatsapp_group_link = fields.Char(string='WhatsApp Group Link')
    telegram_group_link = fields.Char(string='Telegram Group Link')

    # --------------------------------------------------
    # Additional Information
    # --------------------------------------------------

    notes = fields.Text(string='Notes')

    created_date = fields.Datetime(
        string='Created Date',
        default=fields.Datetime.now
    )
    program_ids = fields.Many2many(
        "umrah.transport.program",
        string="Transport Programs",
        compute="_compute_program_ids",
    )
    program_count = fields.Integer(
        string="Programs",
        compute="_compute_program_count"
    )
    hotel_booking_count = fields.Integer(
        string="Hotel Bookings",
        compute="_compute_hotel_booking_count"
    )
    pilgrim_count = fields.Integer(
        string="Pilgrims",
        compute="_compute_pilgrim_count"
    )
    invoice_count = fields.Integer(
        string="Invoices",
        compute="_compute_invoice_count"
    )

    def _compute_invoice_count(self):
        for rec in self:
            rec.invoice_count = self.env["account.move"].search_count([
                ("move_type", "=", "out_invoice"),
                "|",
                ("umrah_group_id", "=", rec.id),
                "&",
                ("umrah_contract_id", "=", rec.contract_id.id),
                ("umrah_group_id", "=", False),
            ])
    @api.depends('member_ids.pilgrim_id')
    def _compute_pilgrim_count(self):
        for rec in self:
            rec.pilgrim_count = len(rec.member_ids.mapped('pilgrim_id'))

    @api.depends('hotel_booking_ids')
    def _compute_hotel_booking_count(self):
        for rec in self:
            rec.hotel_booking_count = len(rec.hotel_booking_ids)


    @api.depends()
    def _compute_program_ids(self):
        for group in self:
            programs = self.env["umrah.transport.program"].search([
                ("group_ids", "in", group.id)
            ])
            group.program_ids = programs

    def _compute_program_count(self):
        for rec in self:
            rec.program_count = len(rec.program_ids)

    def action_view_programs(self):
        self.ensure_one()

        return {
            "type": "ir.actions.act_window",
            "name": "Transport Programs",
            "res_model": "umrah.transport.program",
            "view_mode": "list,form",
            "domain": [("group_ids", "in", self.id)],
        }

    # --------------------------------------------------
    # COMPUTE METHODS
    # --------------------------------------------------

    @api.depends('member_ids')
    def _compute_current_members(self):
        for record in self:
            record.current_members = len(record.member_ids)

    @api.depends('max_capacity', 'current_members')
    def _compute_available_spots(self):
        for record in self:
            record.available_spots = max(
                record.max_capacity - record.current_members,
                0
            )

    @api.depends('trip_ids')
    def _compute_trip_count(self):
        for rec in self:
            rec.trip_count = len(rec.trip_ids)

    # --------------------------------------------------
    # CRUD
    # --------------------------------------------------

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('code'):
                vals['code'] = self.env['ir.sequence'].next_by_code('umrah.group') or 'New'
        return super(UmrahGroup, self).create(vals_list)

    def _hotel_coverage_ok(self):
        """Masar rule: confirmed hotel bookings must cover the whole stay
        (arrival date to departure date) with no gaps. Returns True when the
        group has no dates set (nothing to check yet)."""
        self.ensure_one()
        if not self.arrival_date or not self.departure_date:
            return True
        bookings = self.hotel_booking_ids.filtered(
            lambda b: b.status != 'cancelled'
            and b.check_in_date and b.check_out_date)
        if not bookings:
            return False
        covered_until = self.arrival_date
        for booking in bookings.sorted(key=lambda b: b.check_in_date):
            if booking.check_in_date > covered_until:
                return False  # gap in coverage
            covered_until = max(covered_until, booking.check_out_date)
        return covered_until >= self.departure_date

    # --------------------------------------------------
    # Actions
    # --------------------------------------------------

    def action_confirm(self):
        self.status = 'confirmed'

    def action_depart(self):
        self.status = 'departed'

    def action_arrive_saudi(self):
        self.status = 'in_saudi'

    def action_return(self):
        self.status = 'returned'

    def action_complete(self):
        self.status = 'completed'

    def action_cancel(self):
        self.status = 'cancelled'

    def action_view_trips(self):
        self.ensure_one()

        return {
            'type': 'ir.actions.act_window',
            'name': 'Trips',
            'res_model': 'umrah.trip',
            'view_mode': 'list,form',
            'domain': [('id', 'in', self.trip_ids.ids)],
        }

    def action_view_hotel_bookings(self):
        self.ensure_one()

        return {
            "type": "ir.actions.act_window",
            "name": "Hotel Bookings",
            "res_model": "umrah.hotel.booking",
            "view_mode": "list,form",
            "domain": [("group_ids", "in", self.id)],
        }

    def action_view_pilgrims(self):
        self.ensure_one()

        pilgrim_ids = self.member_ids.mapped('pilgrim_id').ids

        return {
            "type": "ir.actions.act_window",
            "name": "Pilgrims",
            "res_model": "umrah.pilgrim",
            "view_mode": "list,form",
            "domain": [("id", "in", pilgrim_ids)],
        }

    def action_view_invoices(self):
        self.ensure_one()

        return {
            "type": "ir.actions.act_window",
            "name": "Invoices",
            "res_model": "account.move",
            "view_mode": "list,form",
            "domain": [
                ("move_type", "=", "out_invoice"),
                "|",
                ("umrah_group_id", "=", self.id),
                "&",
                ("umrah_contract_id", "=", self.contract_id.id),
                ("umrah_group_id", "=", False),
            ],
        }



    # --------------------------------------------------
    # Constraints
    # --------------------------------------------------

    @api.constrains('max_capacity')
    def _check_capacity(self):
        for record in self:
            if record.max_capacity <= 0:
                raise ValidationError("Maximum capacity must be greater than zero.")

    @api.constrains('current_members', 'max_capacity')
    def _check_members_capacity(self):
        for record in self:
            if record.current_members > record.max_capacity:
                raise ValidationError(
                    "Current members cannot exceed maximum capacity."
                )

    # deprecated: button removed in Section 1.1 (kept in case anything calls it internally)
    def action_create_invoice(self):
        self.ensure_one()

        contract = self.contract_id

        if not contract:
            raise ValidationError(
                _("You must set a Contract before creating the invoice.")
            )

        if not contract.analytic_plan_id or not contract.analytic_account_id:
            raise ValidationError(
                _("The linked Contract must have Analytic Plan and Analytic Account configured before creating the invoice.")
            )

        customer = contract.customer_id
        partner = customer.company_partner_id

        analytic_account = contract.analytic_account_id

        invoice_vals = {
            "move_type": "out_invoice",
            "partner_id": partner.id,
            "umrah_contract_id": contract.id,
            "umrah_group_id": self.id,
            "umrah_customer_id": customer.id,
            "invoice_line_ids": [(0, 0, {
                "name": f"Umrah Group {self.name}",
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

    def action_print_pilgrim_cards(self):
        self.ensure_one()

        pilgrims = self.member_ids.mapped("pilgrim_id")

        if not pilgrims:
            raise ValidationError(_("No pilgrims found in this group."))

        return self.env.ref(
            "era_nusuk.action_report_pilgrim_card"
        ).report_action(pilgrims)
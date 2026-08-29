# -*- coding: utf-8 -*-

from odoo import models, fields, api, _
from odoo.exceptions import ValidationError


class UmrahTrip(models.Model):
    _name = 'umrah.trip'
    _description = 'Umrah Trip'
    _rec_name = 'name'
    _inherit = ['mail.thread','mail.activity.mixin']

    # --------------------------------------------------
    # Basic Information
    # --------------------------------------------------

    name = fields.Char(string='Trip Name', required=True)
    code = fields.Char(string='Trip Code', required=True, copy=False,
                       default=lambda self: _('New'))
    description = fields.Text(string='Description')

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('code') or vals['code'] == _('New'):
                vals['code'] = self.env['ir.sequence'].next_by_code('umrah.trip') or _('New')
        return super().create(vals_list)

    # --------------------------------------------------
    # Dates
    # --------------------------------------------------

    departure_date = fields.Datetime(string='Departure Date', required=True)
    return_date = fields.Datetime(string='Return Date', required=True)

    duration_days = fields.Integer(
        string='Duration (Days)',
        compute='_compute_duration',
        store=True
    )

    # --------------------------------------------------
    # Locations
    # --------------------------------------------------

    departure_city = fields.Char(string='Departure City', required=True)
    departure_airport = fields.Char(string='Departure Airport')

    arrival_city = fields.Char(
        string='Arrival City',
        default='Jeddah'
    )

    arrival_airport = fields.Char(
        string='Arrival Airport',
        default='King Abdulaziz International Airport'
    )

    # --------------------------------------------------
    # Capacity and Pricing
    # --------------------------------------------------

    max_capacity = fields.Integer(string='Maximum Capacity', required=True)

    current_bookings = fields.Integer(
        string='Current Bookings',
        compute='_compute_current_bookings'
    )

    available_seats = fields.Integer(
        string='Available Seats',
        compute='_compute_available_seats',
        store=True
    )

    base_price = fields.Float(string='Base Price', required=True)

    # --------------------------------------------------
    # Package
    # --------------------------------------------------

    package_type = fields.Selection(
        [
            ('economy', 'Economy'),
            ('standard', 'Standard'),
            ('premium', 'Premium'),
            ('vip', 'VIP')
        ],
        string='Package Type',
        required=True,
        default='standard'
    )

    includes_flight = fields.Boolean(string='Includes Flight', default=True)
    includes_accommodation = fields.Boolean(string='Includes Accommodation', default=True)
    includes_transportation = fields.Boolean(string='Includes Transportation', default=True)
    includes_meals = fields.Boolean(string='Includes Meals', default=False)
    includes_guide = fields.Boolean(string='Includes Guide', default=True)

    # --------------------------------------------------
    # Status
    # --------------------------------------------------

    status = fields.Selection(
        [
            ('draft', 'Draft'),
            ('confirmed', 'Confirmed'),
            ('in_progress', 'In Progress'),
            ('completed', 'Completed'),
            ('cancelled', 'Cancelled')
        ],
        string='Status',
        default='draft'
    )

    # --------------------------------------------------
    # Relations
    # --------------------------------------------------

    group_ids = fields.Many2many(
        'umrah.group',
        'umrah_group_trip_rel',
        'trip_id',
        'group_id',
        string='Groups'
    )

    group_count = fields.Integer(
        string="Groups",
        compute="_compute_group_count"
    )

    # 🔹 الجدول الوسيط
    member_ids = fields.One2many(
        'umrah.group.member',
        'trip_id',
        string='Members'
    )

    # 🔹 المعتمرين في الرحلة
    pilgrim_ids = fields.Many2many(
        'umrah.pilgrim',
        compute='_compute_pilgrims',
        string='Pilgrims'
    )
    pilgrim_count = fields.Integer(
        compute="_compute_pilgrim_count",
        string="Pilgrims"
    )
    booking_count = fields.Integer(
        compute="_compute_booking_count",
        string="Hotel Bookings"
    )


    hotel_ids = fields.Many2many('umrah.hotel', string='Hotels')

    agent_id = fields.Many2one(
        'umrah.agent',
        string='Agent'
    )

    # --------------------------------------------------
    # Visits
    # --------------------------------------------------

    mecca_visits = fields.Text(string='Mecca Visits Schedule')
    medina_visits = fields.Text(string='Medina Visits Schedule')
    other_visits = fields.Text(string='Other Religious Sites')

    # --------------------------------------------------
    # Additional Information
    # --------------------------------------------------

    notes = fields.Text(string='Notes')

    created_date = fields.Datetime(
        string='Created Date',
        default=fields.Datetime.now
    )

    # --------------------------------------------------
    # COMPUTE METHODS
    # --------------------------------------------------
    @api.depends('member_ids')
    def _compute_pilgrim_count(self):
        for rec in self:
            rec.pilgrim_count = len(rec.member_ids.mapped("pilgrim_id"))

    @api.depends('group_ids')
    def _compute_booking_count(self):
        Booking = self.env["umrah.hotel.booking"]

        for rec in self:
            bookings = Booking.search([
                ("group_ids", "in", rec.group_ids.ids)
            ])
            rec.booking_count = len(bookings)

    @api.depends('group_ids')
    def _compute_group_count(self):
        for rec in self:
            rec.group_count = len(rec.group_ids)

    @api.depends('member_ids')
    def _compute_pilgrims(self):
        for trip in self:
            trip.pilgrim_ids = trip.member_ids.mapped('pilgrim_id')

    @api.depends('departure_date', 'return_date')
    def _compute_duration(self):
        for record in self:
            if record.departure_date and record.return_date:
                delta = record.return_date - record.departure_date
                record.duration_days = delta.days
            else:
                record.duration_days = 0

    @api.depends('member_ids')
    def _compute_current_bookings(self):
        for record in self:
            record.current_bookings = len(record.member_ids)

    @api.depends('max_capacity', 'current_bookings')
    def _compute_available_seats(self):
        for record in self:
            record.available_seats = max(
                record.max_capacity - record.current_bookings,
                0
            )

    # --------------------------------------------------
    # SMART BUTTON ACTION
    # --------------------------------------------------

    def action_view_groups(self):
        self.ensure_one()

        return {
            'type': 'ir.actions.act_window',
            'name': 'Groups',
            'res_model': 'umrah.group',
            'view_mode': 'list,form',
            'domain': [('id', 'in', self.group_ids.ids)],
        }

    def action_view_pilgrims(self):
        self.ensure_one()

        pilgrim_ids = self.member_ids.mapped("pilgrim_id").ids

        return {
            "type": "ir.actions.act_window",
            "name": "Pilgrims",
            "res_model": "umrah.pilgrim",
            "view_mode": "list,form",
            "domain": [("id", "in", pilgrim_ids)],
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

    # --------------------------------------------------
    # Constraints
    # --------------------------------------------------

    @api.constrains('departure_date', 'return_date')
    def _check_dates(self):
        for record in self:
            if record.departure_date and record.return_date:
                if record.return_date <= record.departure_date:
                    raise ValidationError(
                        "Return date must be after departure date."
                    )
# -*- coding: utf-8 -*-

from odoo import models, fields, api
from odoo.exceptions import ValidationError


class UmrahHotel(models.Model):
    _name = 'umrah.hotel'
    _description = 'Umrah Hotel'
    _rec_name = 'name'
    _inherit = ['mail.thread','mail.activity.mixin']

    # Basic Information
    name = fields.Char(string='Hotel Name', required=True)
    code = fields.Char(string='Hotel Code')
    description = fields.Text(string='Description')

    # Masar rule: only Ministry-of-Tourism licensed hotels registered on the
    # platform can back a visa-qualifying booking.
    tourism_license_number = fields.Char(
        string='Tourism License No.', copy=False)
    
    # Location
    city = fields.Selection([
        ('mecca', 'Mecca'),
        ('medina', 'Medina'),
        ('jeddah', 'Jeddah'),
        ('other', 'Other')
    ], string='City', required=True)
    address = fields.Text(string='Address', required=True)
    distance_to_haram = fields.Float(string='Distance to Haram (meters)')
    
    # Contact Information
    phone = fields.Char(string='Phone')
    email = fields.Char(string='Email')
    website = fields.Char(string='Website')
    contact_person = fields.Char(string='Contact Person')
    
    # Hotel Details
    star_rating = fields.Selection([
        ('1', '1 Star'),
        ('2', '2 Stars'),
        ('3', '3 Stars'),
        ('4', '4 Stars'),
        ('5', '5 Stars')
    ], string='Star Rating')
    
    total_rooms = fields.Integer(string='Total Rooms')
    
    # Facilities
    has_wifi = fields.Boolean(string='WiFi Available', default=True)
    has_parking = fields.Boolean(string='Parking Available')
    has_restaurant = fields.Boolean(string='Restaurant Available')
    has_gym = fields.Boolean(string='Gym Available')
    has_spa = fields.Boolean(string='Spa Available')
    has_laundry = fields.Boolean(string='Laundry Service')
    has_room_service = fields.Boolean(string='Room Service')
    has_shuttle = fields.Boolean(string='Shuttle Service to Haram')
    
    # Relations
    room_type_ids = fields.One2many('umrah.hotel.room.type', 'hotel_id', string='Room Types')
    booking_ids = fields.One2many('umrah.hotel.booking', 'hotel_id', string='Bookings')
    trip_ids = fields.Many2many('umrah.trip', string='Trips')
    
    # Status
    is_active = fields.Boolean(string='Active', default=True)
    
    # Additional Information
    notes = fields.Text(string='Notes')
    created_date = fields.Datetime(string='Created Date', default=fields.Datetime.now)


class UmrahHotelRoomType(models.Model):
    _name = 'umrah.hotel.room.type'
    _description = 'Hotel Room Type'
    _rec_name = 'name'

    # Basic Information
    name = fields.Char(string='Room Type Name', required=True)
    code = fields.Char(string='Room Type Code')
    description = fields.Text(string='Description')
    
    # Room Details
    hotel_id = fields.Many2one('umrah.hotel', string='Hotel', required=True)
    capacity = fields.Integer(string='Capacity (Persons)', required=True)
    bed_type = fields.Selection([
        ('single', 'Single Bed'),
        ('double', 'Double Bed'),
        ('twin', 'Twin Beds'),
        ('triple', 'Triple Beds'),
        ('quad', 'Quad Beds')
    ], string='Bed Type', required=True)
    
    room_size = fields.Float(string='Room Size (sqm)')
    has_balcony = fields.Boolean(string='Has Balcony')
    has_view = fields.Selection([
        ('haram', 'Haram View'),
        ('city', 'City View'),
        ('courtyard', 'Courtyard View'),
        ('no_view', 'No Special View')
    ], string='View Type')
    
    # Amenities
    has_ac = fields.Boolean(string='Air Conditioning', default=True)
    has_tv = fields.Boolean(string='Television', default=True)
    has_minibar = fields.Boolean(string='Mini Bar')
    has_safe = fields.Boolean(string='Safe')
    has_bathroom = fields.Boolean(string='Private Bathroom', default=True)
    
    # Pricing
    base_price_per_night = fields.Float(string='Base Price per Night', required=True)
    
    # Availability
    total_rooms = fields.Integer(string='Total Rooms of This Type')
    
    # Relations
    booking_ids = fields.One2many('umrah.hotel.booking', 'room_type_id', string='Bookings')
    
    # Status
    is_active = fields.Boolean(string='Active', default=True)


class UmrahHotelBooking(models.Model):
    _name = 'umrah.hotel.booking'
    _description = 'Hotel Booking'
    _rec_name = 'booking_reference'

    # Basic Information
    booking_reference = fields.Char(string='Booking Reference', required=True, default=lambda self: self.env['ir.sequence'].next_by_code('umrah.hotel.booking'))
    
    # Relations
    hotel_id = fields.Many2one('umrah.hotel', string='Hotel', required=True)
    room_type_id = fields.Many2one('umrah.hotel.room.type', string='Room Type', required=True)
    group_ids = fields.Many2many(
        "umrah.group",
        "umrah_hotel_booking_group_rel",
        "booking_id",
        "group_id",
        string="Groups",
        required=True
    )
    member_ids = fields.Many2many(
        "umrah.group.member",
        compute="_compute_members",
        string="Members",
        store=True
    )
    
    # Dates
    check_in_date = fields.Date(string='Check-in Date', required=True)
    check_out_date = fields.Date(string='Check-out Date', required=True)
    nights = fields.Integer(string='Number of Nights', compute='_compute_nights', store=True)
    
    # Pricing
    room_rate_per_night = fields.Float(string='Room Rate per Night', required=True)
    total_room_cost = fields.Float(string='Total Room Cost', compute='_compute_total_cost', store=True)
    
    # Meals
    meal_plan = fields.Selection([
        ('no_meals', 'No Meals'),
        ('breakfast', 'Breakfast Only'),
        ('half_board', 'Half Board (Breakfast + Dinner)'),
        ('full_board', 'Full Board (All Meals)')
    ], string='Meal Plan', default='no_meals')
    meal_cost_per_day = fields.Float(string='Meal Cost per Day')
    total_meal_cost = fields.Float(string='Total Meal Cost', compute='_compute_total_cost', store=True)
    
    # Total
    total_cost = fields.Float(string='Total Cost', compute='_compute_total_cost', store=True)
    
    # Status
    status = fields.Selection([
        ('draft', 'Draft'),
        ('confirmed', 'Confirmed'),
        ('checked_in', 'Checked In'),
        ('checked_out', 'Checked Out'),
        ('cancelled', 'Cancelled')
    ], string='Status', default='draft')
    
    # Masar approval (mandatory before visa issuance since June 2025)
    masar_approval_ref = fields.Char(string='Masar Approval Ref', copy=False)
    masar_state = fields.Selection([
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ], string='Masar Status', default='pending')

    # Rooming list
    room_ids = fields.One2many(
        'umrah.hotel.booking.room', 'booking_id', string='Rooms')
    room_count = fields.Integer(compute='_compute_rooming', string='Rooms')
    unassigned_member_count = fields.Integer(
        compute='_compute_rooming', string='Unassigned Members')

    def _compute_rooming(self):
        for rec in self:
            rec.room_count = len(rec.room_ids)
            assigned = rec.room_ids.mapped('member_ids')
            rec.unassigned_member_count = len(rec.member_ids - assigned)

    # Additional Information
    special_requests = fields.Text(string='Special Requests')
    notes = fields.Text(string='Notes')
    created_date = fields.Datetime(string='Created Date', default=fields.Datetime.now)
    group_count = fields.Integer(
        compute="_compute_counts",
        string="Groups"
    )

    member_count = fields.Integer(
        compute="_compute_counts",
        string="Members"
    )

    pilgrim_count = fields.Integer(
        compute="_compute_counts",
        string="Pilgrims"
    )

    trip_count = fields.Integer(
        compute="_compute_counts",
        string="Trips"
    )

    def _compute_counts(self):
        for rec in self:
            groups = rec.group_ids
            members = rec.member_ids
            pilgrims = members.mapped("pilgrim_id")
            trips = groups.mapped("trip_ids")

            rec.group_count = len(groups)
            rec.member_count = len(members)
            rec.pilgrim_count = len(pilgrims)
            rec.trip_count = len(trips)

    @api.depends("group_ids", "group_ids.member_ids")
    def _compute_members(self):
        for booking in self:
            members = booking.group_ids.mapped("member_ids")
            booking.member_ids = members
    
    @api.depends('check_in_date', 'check_out_date')
    def _compute_nights(self):
        for record in self:
            if record.check_in_date and record.check_out_date:
                delta = record.check_out_date - record.check_in_date
                record.nights = delta.days
            else:
                record.nights = 0
    
    @api.depends('nights', 'room_rate_per_night', 'meal_cost_per_day')
    def _compute_total_cost(self):
        for record in self:
            record.total_room_cost = record.nights * record.room_rate_per_night
            record.total_meal_cost = record.nights * record.meal_cost_per_day
            record.total_cost = record.total_room_cost + record.total_meal_cost
    
    @api.onchange('room_type_id')
    def _onchange_room_type(self):
        if self.room_type_id:
            self.room_rate_per_night = self.room_type_id.base_price_per_night

    def action_view_groups(self):
        self.ensure_one()

        return {
            "type": "ir.actions.act_window",
            "name": "Groups",
            "res_model": "umrah.group",
            "view_mode": "list,form",
            "domain": [("id", "in", self.group_ids.ids)],
        }

    def action_view_members(self):
        self.ensure_one()

        return {
            "type": "ir.actions.act_window",
            "name": "Members",
            "res_model": "umrah.group.member",
            "view_mode": "list,form",
            "domain": [("id", "in", self.member_ids.ids)],
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

    def action_view_trips(self):
        self.ensure_one()

        trip_ids = self.group_ids.mapped("trip_ids").ids

        return {
            "type": "ir.actions.act_window",
            "name": "Trips",
            "res_model": "umrah.trip",
            "view_mode": "list,form",
            "domain": [("id", "in", trip_ids)],
        }



class UmrahHotelBookingRoom(models.Model):
    """Rooming list line: one physical room within a booking, with the
    group members assigned to it. Capacity comes from the configurable
    occupancy type."""
    _name = 'umrah.hotel.booking.room'
    _description = 'Hotel Booking Room'
    _order = 'room_number'

    booking_id = fields.Many2one(
        'umrah.hotel.booking', string='Booking',
        required=True, ondelete='cascade')
    room_number = fields.Char(string='Room No.', required=True)
    room_type_id = fields.Many2one(
        'umrah.hotel.room.type', string='Room Type',
        domain="[('hotel_id', '=', hotel_id)]")
    hotel_id = fields.Many2one(
        related='booking_id.hotel_id', store=True)
    occupancy_type_id = fields.Many2one(
        'umrah.occupancy.type', string='Occupancy', required=True)
    capacity = fields.Integer(
        related='occupancy_type_id.capacity', string='Capacity')
    member_ids = fields.Many2many(
        'umrah.group.member', string='Members',
        domain="[('group_id', 'in', group_ids)]")
    group_ids = fields.Many2many(
        related='booking_id.group_ids')
    occupants = fields.Integer(
        compute='_compute_occupants', string='Occupants')

    _sql_constraints = [
        ('room_uniq_per_booking', 'unique(booking_id, room_number)',
         'Room number already used in this booking.'),
    ]

    @api.depends('member_ids')
    def _compute_occupants(self):
        for rec in self:
            rec.occupants = len(rec.member_ids)

    @api.constrains('member_ids', 'occupancy_type_id')
    def _check_capacity(self):
        for rec in self:
            if rec.occupancy_type_id and \
                    len(rec.member_ids) > rec.occupancy_type_id.capacity:
                raise ValidationError(
                    "Room %s exceeds its capacity (%s beds)." % (
                        rec.room_number, rec.occupancy_type_id.capacity))

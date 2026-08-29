# -*- coding: utf-8 -*-

from odoo import models, fields, api, _
from datetime import datetime, timedelta
from odoo.exceptions import UserError


class UmrahBulkVisaWizard(models.TransientModel):
    _name = 'umrah.bulk.visa.wizard'
    _description = 'Bulk Visa Application Wizard'

    trip_id = fields.Many2one('umrah.trip', string='Trip', required=True)
    group_id = fields.Many2one('umrah.group', string='Group')
    visa_type = fields.Selection([
        ('umrah', 'Umrah Visa'),
        ('hajj', 'Hajj Visa'),
        ('visit', 'Visit Visa'),
        ('business', 'Business Visa')
    ], string='Visa Type', required=True, default='umrah')

    consulate_location = fields.Char(string='Consulate/Embassy Location', required=True)
    visa_fee = fields.Float(string='Visa Fee per Person', required=True)
    service_fee = fields.Float(string='Service Fee per Person', required=True)
    agent_id = fields.Many2one('umrah.agent', string='Processing Agent')

    def action_create_visas(self):
        """Create visa applications for all pilgrims in the selected trip/group"""
        pilgrim_domain = [('trip_id', '=', self.trip_id.id)]
        if self.group_id:
            pilgrim_domain.append(('group_id', '=', self.group_id.id))

        pilgrims = self.env['umrah.pilgrim'].search(pilgrim_domain)

        visa_vals_list = []
        for pilgrim in pilgrims:
            # Check if pilgrim already has a visa
            existing_visa = self.env['umrah.visa'].search([
                ('pilgrim_id', '=', pilgrim.id),
                ('status', 'not in', ['cancelled', 'rejected'])
            ], limit=1)

            if not existing_visa:
                visa_vals = {
                    'pilgrim_id': pilgrim.id,
                    'trip_id': self.trip_id.id,
                    'visa_type': self.visa_type,
                    'consulate_location': self.consulate_location,
                    'visa_fee': self.visa_fee,
                    'service_fee': self.service_fee,
                    'agent_id': self.agent_id.id if self.agent_id else False,
                    'application_date': fields.Date.today(),
                }
                visa_vals_list.append(visa_vals)

        if visa_vals_list:
            created_visas = self.env['umrah.visa'].create(visa_vals_list)
            return {
                'type': 'ir.actions.act_window',
                'name': 'Created Visas',
                'res_model': 'umrah.visa',
                'view_mode': 'list,form',
                'domain': [('id', 'in', created_visas.ids)],
                'context': {'create': False}
            }
        else:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'No Visas Created',
                    'message': 'All selected pilgrims already have visa applications.',
                    'type': 'warning'
                }
            }


class UmrahBulkHotelBookingWizard(models.TransientModel):
    _name = 'umrah.bulk.hotel.booking.wizard'
    _description = 'Bulk Hotel Booking Wizard'

    trip_id = fields.Many2one('umrah.trip', string='Trip', required=True)
    group_id = fields.Many2one('umrah.group', string='Group')
    hotel_id = fields.Many2one('umrah.hotel', string='Hotel', required=True)
    room_type_id = fields.Many2one('umrah.hotel.room.type', string='Room Type', required=True)

    check_in_date = fields.Date(string='Check-in Date', required=True)
    check_out_date = fields.Date(string='Check-out Date', required=True)

    meal_plan = fields.Selection([
        ('no_meals', 'No Meals'),
        ('breakfast', 'Breakfast Only'),
        ('half_board', 'Half Board (Breakfast + Dinner)'),
        ('full_board', 'Full Board (All Meals)')
    ], string='Meal Plan', default='no_meals')
    meal_cost_per_day = fields.Float(string='Meal Cost per Day')
    room_rate_per_night = fields.Float(string='Room Rate per Night')

    @api.onchange('hotel_id')
    def _onchange_hotel_id(self):
        if self.hotel_id:
            return {'domain': {'room_type_id': [('hotel_id', '=', self.hotel_id.id)]}}
        else:
            return {'domain': {'room_type_id': []}}

    @api.onchange('room_type_id')
    def _onchange_room_type_id(self):
        if self.room_type_id:
            self.room_rate_per_night = self.room_type_id.base_price_per_night

    def action_create_bookings(self):
        """Create one hotel booking per group of the selected trip/group.

        umrah.hotel.booking is a group-level record (required ``group_ids``
        M2M); the Masar hotel-coverage check reads it per group.
        """
        self.ensure_one()
        if self.check_out_date <= self.check_in_date:
            raise UserError(_("Check-out date must be after check-in date."))

        groups = self.group_id or self.trip_id.group_ids
        if not groups:
            raise UserError(_(
                "No group is linked to the selected trip. Hotel bookings are "
                "created per group, so add the group to the trip first."))

        rate = self.room_rate_per_night or self.room_type_id.base_price_per_night
        created_bookings = self.env['umrah.hotel.booking'].create([{
            'hotel_id': self.hotel_id.id,
            'room_type_id': self.room_type_id.id,
            'group_ids': [(6, 0, [group.id])],
            'check_in_date': self.check_in_date,
            'check_out_date': self.check_out_date,
            'room_rate_per_night': rate,
            'meal_plan': self.meal_plan,
            'meal_cost_per_day': self.meal_cost_per_day,
        } for group in groups])
        return {
            'type': 'ir.actions.act_window',
            'name': _('Created Bookings'),
            'res_model': 'umrah.hotel.booking',
            'view_mode': 'list,form',
            'domain': [('id', 'in', created_bookings.ids)],
            'context': {'create': False}
        }


class UmrahTripStatusUpdateWizard(models.TransientModel):
    _name = 'umrah.trip.status.update.wizard'
    _description = 'Trip Status Update Wizard'

    trip_id = fields.Many2one('umrah.trip', string='Trip', required=True)
    new_status = fields.Selection([
        ('confirmed', 'Confirmed'),
        ('in_progress', 'In Progress'),
        ('completed', 'Completed'),
        ('cancelled', 'Cancelled')
    ], string='New Status', required=True)

    update_pilgrims = fields.Boolean(string='Update Pilgrim Status', default=True)
    update_groups = fields.Boolean(string='Update Group Status', default=True)

    pilgrim_status = fields.Selection([
        ('confirmed', 'Confirmed'),
        ('visa_applied', 'Visa Applied'),
        ('visa_approved', 'Visa Approved'),
        ('departed', 'Departed'),
        ('returned', 'Returned'),
        ('cancelled', 'Cancelled')
    ], string='Pilgrim Status')

    group_status = fields.Selection([
        ('confirmed', 'Confirmed'),
        ('departed', 'Departed'),
        ('in_saudi', 'In Saudi Arabia'),
        ('returned', 'Returned'),
        ('completed', 'Completed'),
        ('cancelled', 'Cancelled')
    ], string='Group Status')

    def action_update_status(self):
        """Update trip status and optionally update related pilgrims and groups"""
        self.trip_id.status = self.new_status

        if self.update_pilgrims and self.pilgrim_status:
            self.trip_id.pilgrim_ids.write({'status': self.pilgrim_status})

        if self.update_groups and self.group_status:
            self.trip_id.group_ids.write({'status': self.group_status})

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Status Updated',
                'message': f'Trip status updated to {self.new_status}',
                'type': 'success'
            }
        }


class UmrahStatisticsWizard(models.TransientModel):
    _name = 'umrah.statistics.wizard'
    _description = 'Umrah Statistics Wizard'

    date_from = fields.Date(string='Date From', required=True,
                            default=lambda self: fields.Date.today().replace(month=1, day=1))
    date_to = fields.Date(string='Date To', required=True, default=fields.Date.today)

    trip_id = fields.Many2one('umrah.trip', string='Specific Trip')
    agent_id = fields.Many2one('umrah.agent', string='Specific Agent')

    def action_generate_statistics(self):
        """Generate comprehensive statistics report"""
        domain = [
            ('registration_date', '>=', self.date_from),
            ('registration_date', '<=', self.date_to)
        ]

        if self.trip_id:
            domain.append(('trip_id', '=', self.trip_id.id))

        pilgrims = self.env['umrah.pilgrim'].search(domain)

        # Calculate statistics
        total_pilgrims = len(pilgrims)
        male_pilgrims = len(pilgrims.filtered(lambda p: p.gender == 'male'))
        female_pilgrims = len(pilgrims.filtered(lambda p: p.gender == 'female'))

        # Status breakdown
        status_stats = {}
        for status in ['draft', 'confirmed', 'visa_applied', 'visa_approved', 'departed', 'returned', 'cancelled']:
            status_stats[status] = len(pilgrims.filtered(lambda p: p.status == status))

        # Nationality breakdown
        nationality_stats = {}
        for pilgrim in pilgrims:
            country = pilgrim.nationality.name if pilgrim.nationality else 'Unknown'
            nationality_stats[country] = nationality_stats.get(country, 0) + 1

        # Trip statistics
        trip_domain = [
            ('departure_date', '>=', self.date_from),
            ('departure_date', '<=', self.date_to)
        ]
        if self.trip_id:
            trip_domain.append(('id', '=', self.trip_id.id))

        trips = self.env['umrah.trip'].search(trip_domain)
        total_trips = len(trips)

        # Visa statistics
        visa_domain = [
            ('application_date', '>=', self.date_from),
            ('application_date', '<=', self.date_to)
        ]
        visas = self.env['umrah.visa'].search(visa_domain)

        visa_stats = {}
        for status in ['draft', 'submitted', 'under_review', 'approved', 'rejected', 'issued']:
            visa_stats[status] = len(visas.filtered(lambda v: v.status == status))

        # Create a summary report
        report_data = {
            'date_from': self.date_from,
            'date_to': self.date_to,
            'total_pilgrims': total_pilgrims,
            'male_pilgrims': male_pilgrims,
            'female_pilgrims': female_pilgrims,
            'total_trips': total_trips,
            'status_stats': status_stats,
            'nationality_stats': nationality_stats,
            'visa_stats': visa_stats,
        }

        # You can extend this to create a proper report view or export to Excel
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Statistics Generated',
                'message': f'Statistics for {total_pilgrims} pilgrims and {total_trips} trips generated.',
                'type': 'success'
            }
        }


class UmrahBulkInvoiceVisaWizard(models.TransientModel):
    _name = 'umrah.bulk.invoice.visa.wizard'
    _description = 'Bulk Invoice Visa Application Wizard'

    trip_id = fields.Many2one('umrah.trip', string='Trip')
    group_id = fields.Many2one('umrah.group', string='Group')
    visa_type = fields.Selection([
        ('umrah', 'Umrah Visa'),
        ('hajj', 'Hajj Visa'),
        ('visit', 'Visit Visa'),
        ('business', 'Business Visa')
    ], string='Visa Type', )

    status = fields.Selection([
        ('draft', 'Draft'),
        ('submitted', 'Submitted'),
        ('under_review', 'Under Review'),
        ('approved', 'Approved'),
        ('issued', 'Issued'),
        ('invoiced', 'Invoiced'),
    ], string='Status',)

    agent_id = fields.Many2one('umrah.agent', string='Processing Agent', required=True)

    def action_create_bulk_agent_invoices(self):
        domain = [('agent_id', '=', self.agent_id.id)]
        if self.group_id:
            domain.append(('group_id', '=', self.group_id.id))
        if self.trip_id:
            domain.append(('trip_id', '=', self.trip_id.id))
        if self.visa_type:
            domain.append(('visa_type', '=', self.visa_type))
        if self.status:
            domain.append(('status', '=', self.status))
        else:
            # Never re-invoice or bill dead visas unless explicitly asked to.
            domain.append(('status', 'not in', ('cancelled', 'rejected', 'invoiced')))

        selected_visas = self.env['umrah.visa'].search(domain)

        if not selected_visas:
            raise UserError(_("There are no visas matching the criteria"))

        return selected_visas._create_agent_invoices()

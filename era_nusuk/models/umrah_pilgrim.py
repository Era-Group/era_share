# -*- coding: utf-8 -*-

from odoo import models, fields, api


class UmrahPilgrim(models.Model):
    _name = 'umrah.pilgrim'
    _description = 'Umrah Pilgrim'
    _rec_name = 'full_name'
    _inherit = ['mail.thread','mail.activity.mixin']

    # Personal Information
    image_1920 = fields.Image(
        string="Avatar",
        max_width=1920,
        max_height=1920
    )
    code = fields.Char(string='Code', readonly=True , default='New')
    full_name = fields.Char(string='Full Name', required=True)
    gender = fields.Selection([
        ('male', 'Male'),
        ('female', 'Female')
    ], string='Gender', required=True)
    birth_date = fields.Date(string='Birth Date', required=True)
    nationality = fields.Many2one('res.country', string='Nationality', required=True)
    
    # Contact Information
    phone = fields.Char(string='Phone Number')
    mobile = fields.Char(string='Mobile Number', required=True)
    email = fields.Char(string='Email')
    address = fields.Text(string='Address')
    
    # Passport Information
    passport_number = fields.Char(string='Passport Number', required=True)
    passport_issue_date = fields.Date(string='Passport Issue Date')
    passport_expiry_date = fields.Date(string='Passport Expiry Date')
    passport_place_of_issue = fields.Char(string='Passport Place of Issue')
    
    # Medical Information
    medical_conditions = fields.Text(string='Medical Conditions')
    emergency_contact_name = fields.Char(string='Emergency Contact Name')
    emergency_contact_phone = fields.Char(string='Emergency Contact Phone')
    
    # Umrah Information
    trip_id = fields.Many2one('umrah.trip', string='Trip')
    group_id = fields.Many2one('umrah.group', string='Group')
    visa_id = fields.Many2one('umrah.visa', string='Visa')
    
    # Status
    status = fields.Selection([
        ('draft', 'Draft'),
        ('confirmed', 'Confirmed'),
        ('visa_applied', 'Visa Applied'),
        ('visa_approved', 'Visa Approved'),
        ('departed', 'Departed'),
        ('returned', 'Returned'),
        ('cancelled', 'Cancelled')
    ], string='Status', default='draft')
    
    # Additional Information
    notes = fields.Text(string='Notes')
    registration_date = fields.Datetime(string='Registration Date', default=fields.Datetime.now)
    partner_id = fields.Many2one('res.partner', string='Partner')
    member_ids = fields.One2many(
        "umrah.group.member",
        "pilgrim_id",
        string="Group Memberships"
    )
    group_count = fields.Integer(
        compute="_compute_related_counts"
    )

    trip_count = fields.Integer(
        compute="_compute_related_counts"
    )

    program_count = fields.Integer(
        compute="_compute_related_counts"
    )

    booking_count = fields.Integer(
        compute="_compute_related_counts"
    )
    id_card_html = fields.Html(
        string="Pilgrim ID Card",
        compute="_compute_id_card_html",
        sanitize=False,
    )

    def _compute_related_counts(self):
        Program = self.env["umrah.transport.program"]
        Booking = self.env["umrah.hotel.booking"]

        for rec in self:
            groups = rec.member_ids.mapped("group_id")
            trips = rec.member_ids.mapped("trip_id")

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

    def action_view_groups(self):
        self.ensure_one()

        groups = self.member_ids.mapped("group_id")

        return {
            "type": "ir.actions.act_window",
            "name": "Groups",
            "res_model": "umrah.group",
            "view_mode": "list,form",
            "domain": [("id", "in", groups.ids)],
        }

    def action_view_trips(self):
        self.ensure_one()

        trips = self.member_ids.mapped("trip_id")

        return {
            "type": "ir.actions.act_window",
            "name": "Trips",
            "res_model": "umrah.trip",
            "view_mode": "list,form",
            "domain": [("id", "in", trips.ids)],
        }

    def action_view_programs(self):
        self.ensure_one()

        groups = self.member_ids.mapped("group_id")

        return {
            "type": "ir.actions.act_window",
            "name": "Programs",
            "res_model": "umrah.transport.program",
            "view_mode": "list,form",
            "domain": [("group_ids", "in", groups.ids)],
        }

    def action_view_bookings(self):
        self.ensure_one()

        groups = self.member_ids.mapped("group_id")

        return {
            "type": "ir.actions.act_window",
            "name": "Hotel Bookings",
            "res_model": "umrah.hotel.booking",
            "view_mode": "list,form",
            "domain": [("group_ids", "in", groups.ids)],
        }

    def create_partner(self, name, email, phone):
        partner_id = self.env['res.partner'].create({
            'name': name,
            'email': email if email else False,
            'phone': phone if phone else False,
        })
        return partner_id.id

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('code') or vals.get('code') == 'New':
                vals['code'] = self.env['ir.sequence'].next_by_code('umrah.pilgrim.code') or 'New'

        records = super().create(vals_list)

        for record in records:
            if not record.partner_id:
                partner_id = record.create_partner(
                    record.full_name,
                    record.email,
                    record.phone
                )
                record.partner_id = partner_id

        return records
    
    def _compute_id_card_html(self):
        for rec in self:
            company = self.env.company

            company_logo_url = (
                f"/web/image/res.company/{company.id}/logo"
                if company and company.id
                else ""
            )

            pilgrim_image_url = (
                f"/web/image/{rec._name}/{rec.id}/image_1920"
                if rec.id and rec.image_1920
                else ""
            )

            gender_label = dict(rec._fields["gender"].selection).get(rec.gender, "") if rec.gender else ""
            nationality_name = rec.nationality.name if rec.nationality else ""
            phone_number = rec.mobile or rec.phone or ""
            passport_number = rec.passport_number or ""
            code = rec.code or ""
            full_name = rec.full_name or ""
            group_name = rec.member_ids[:1].group_id.name if rec.member_ids else ""

            rec.id_card_html = f"""
            <div style="
                width:270px;
                background:white;
                border-radius:16px;
                overflow:hidden;
                border:1px solid #e5e7eb;
                font-family:Arial;
                box-shadow:0 10px 20px rgba(0,0,0,0.08);
            ">

                <!-- Header -->
                <div style="
                    background:#111827;
                    color:white;
                    padding:12px 16px;
                    display:flex;
                    justify-content:space-between;
                    align-items:center;
                ">
                    <img src="{company_logo_url}" style="height:26px;object-fit:contain;">

                   
                </div>


                <!-- Photo -->
                <div style="
                    text-align:center;
                    padding:18px 0 10px 0;
                ">

                    <img src="{pilgrim_image_url}"
                        style="
                        width:100px;
                        height:100px;
                        border-radius:50%;
                        object-fit:cover;
                        border:4px solid #f3f4f6;
                    ">

                    <div style="
                        margin-top:10px;
                        font-size:16px;
                        font-weight:700;
                        color:#111827;
                    ">
                        {full_name}
                    </div>

                    <div style="
                        font-size:13px;
                        color:#6b7280;
                        font-weight:600;
                        margin-top:3px;
                    ">
                        {code}
                    </div>

                </div>
                
                <div style="
    font-size:12px;
    color:#374151;
    margin-top:4px;
    font-weight:500;
">
    {group_name}
</div>


                <!-- Info -->
                <div style="
                    padding:14px 20px 18px 20px;
                    font-size:13px;
                    color:#374151;
                    line-height:1.9;
                ">

                    <div style="display:flex;justify-content:space-between;">
                        <span style="font-weight:600;">Passport</span>
                        <span>{passport_number}</span>
                    </div>

                    <div style="display:flex;justify-content:space-between;">
                        <span style="font-weight:600;">Gender</span>
                        <span>{gender_label}</span>
                    </div>

                    <div style="display:flex;justify-content:space-between;">
                        <span style="font-weight:600;">Nationality</span>
                        <span>{nationality_name}</span>
                    </div>

                    <div style="display:flex;justify-content:space-between;">
                        <span style="font-weight:600;">Phone</span>
                        <span>{phone_number}</span>
                    </div>

                </div>

            </div>
            """
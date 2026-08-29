# -*- coding: utf-8 -*-

from odoo import models, fields, api ,_

from odoo.exceptions import UserError, ValidationError


class UmrahAgent(models.Model):
    _name = 'umrah.agent'
    _description = 'Umrah Agent'
    _rec_name = 'name'
    _inherit = ['mail.thread','mail.activity.mixin']

    # Basic Information
    name = fields.Char(string='Agent Name', required=True)
    code = fields.Char(string='Agent Code', required=True)
    company_name = fields.Char(string='Company Name')
    
    # Contact Information
    phone = fields.Char(string='Phone Number', required=True)
    mobile = fields.Char(string='Mobile Number')
    email = fields.Char(string='Email', required=True)
    website = fields.Char(string='Website')
    
    # Address
    street = fields.Char(string='Street')
    street2 = fields.Char(string='Street 2')
    city = fields.Char(string='City')
    state_id = fields.Many2one('res.country.state', string='State')
    country_id = fields.Many2one('res.country', string='Country', required=True)
    zip = fields.Char(string='ZIP Code')
    
    # Business Information
    license_number = fields.Char(string='License Number')
    license_expiry_date = fields.Date(string='License Expiry Date')
    tax_number = fields.Char(string='Tax Number')
    registration_number = fields.Char(string='Registration Number')
    
    # Agent Type
    agent_type = fields.Selection([
        ('individual', 'Individual Agent'),
        ('company', 'Travel Company'),
        ('tour_operator', 'Tour Operator'),
        ('religious_organization', 'Religious Organization')
    ], string='Agent Type', required=True, default='individual')
    
    # Specialization
    specialization = fields.Selection([
        ('umrah', 'Umrah Only'),
        ('hajj', 'Hajj Only'),
        ('both', 'Umrah & Hajj'),
        ('religious_tourism', 'Religious Tourism'),
        ('general_travel', 'General Travel')
    ], string='Specialization', required=True, default='umrah')
    
    # Experience and Rating
    years_of_experience = fields.Integer(string='Years of Experience')
    rating = fields.Float(string='Rating (1-5)', digits=(2, 1))
    total_pilgrims_served = fields.Integer(string='Total Pilgrims Served', compute='_compute_statistics')
    total_trips_organized = fields.Integer(string='Total Trips Organized', compute='_compute_statistics')
    
    # Financial Information
    commission_rate = fields.Float(string='Commission Rate (%)', digits=(5, 2))
    payment_terms = fields.Selection([
        ('immediate', 'Immediate Payment'),
        ('30_days', '30 Days'),
        ('60_days', '60 Days'),
        ('90_days', '90 Days')
    ], string='Payment Terms', default='30_days')
    
    # Bank Information
    bank_name = fields.Char(string='Bank Name')
    bank_account_number = fields.Char(string='Bank Account Number')
    iban = fields.Char(string='IBAN')
    swift_code = fields.Char(string='SWIFT Code')
    
    # Relations
    trip_ids = fields.One2many('umrah.trip', 'agent_id', string='Trips')
    group_ids = fields.One2many('umrah.group', 'agent_id', string='Groups')
    visa_ids = fields.One2many('umrah.visa', 'agent_id', string='Visas Processed')
    
    # Status and Approval
    status = fields.Selection([
        ('draft', 'Draft'),
        ('pending_approval', 'Pending Approval'),
        ('approved', 'Approved'),
        ('suspended', 'Suspended'),
        ('blacklisted', 'Blacklisted')
    ], string='Status', default='draft', tracking=True)
    
    is_active = fields.Boolean(string='Active', default=True)
    approval_date = fields.Date(string='Approval Date')
    approved_by = fields.Many2one('res.users', string='Approved By')
    
    # Performance Metrics
    success_rate = fields.Float(string='Success Rate (%)', compute='_compute_performance_metrics')
    average_group_size = fields.Float(string='Average Group Size', compute='_compute_performance_metrics')
    customer_satisfaction = fields.Float(string='Customer Satisfaction (1-5)', digits=(2, 1))
    
    # Documents and Certifications
    has_umrah_license = fields.Boolean(string='Has Umrah License')
    has_hajj_license = fields.Boolean(string='Has Hajj License')
    has_travel_license = fields.Boolean(string='Has Travel License')
    has_insurance = fields.Boolean(string='Has Insurance')
    
    # Emergency Contact
    emergency_contact_name = fields.Char(string='Emergency Contact Name')
    emergency_contact_phone = fields.Char(string='Emergency Contact Phone')
    
    # Additional Information
    languages_spoken = fields.Char(string='Languages Spoken')
    services_offered = fields.Text(string='Services Offered')
    notes = fields.Text(string='Notes')
    created_date = fields.Datetime(string='Created Date', default=fields.Datetime.now)
    partner_id = fields.Many2one('res.partner', string='Partner')
    invoice_ids = fields.One2many(comodel_name="account.move", inverse_name="agent_id", string="Invoices", required=False, )

    # --------------------------------------------------
    # Agent Number / Pricing (prerequisites for Visa billing)
    # --------------------------------------------------
    agent_number = fields.Char(string='Agent Number')

    # --------------------------------------------------
    # Nusuk Masar contract & quota
    # --------------------------------------------------
    tier_id = fields.Many2one(
        'umrah.agent.tier', string='Pricing Tier',
        help='Drives which package price lines apply to this agent.')
    masar_contract_number = fields.Char(
        string='Masar Contract No.', copy=False, tracking=True)
    masar_contract_date_from = fields.Date(string='Masar Contract From')
    masar_contract_date_to = fields.Date(string='Masar Contract To')
    seasonal_quota = fields.Integer(
        string='Seasonal Quota',
        help='Maximum visas allowed under the Masar seasonal contract. '
             '0 = unlimited.')
    quota_used = fields.Integer(
        string='Quota Used', compute='_compute_quota')
    quota_remaining = fields.Integer(
        string='Quota Remaining', compute='_compute_quota')

    def _compute_quota(self):
        Visa = self.env['umrah.visa']
        for rec in self:
            domain = [
                ('agent_id', '=', rec.id),
                ('status', 'not in', ('rejected', 'cancelled', 'draft')),
            ]
            if rec.masar_contract_date_from:
                domain.append(
                    ('application_date', '>=', rec.masar_contract_date_from))
            if rec.masar_contract_date_to:
                domain.append(
                    ('application_date', '<=', rec.masar_contract_date_to))
            rec.quota_used = Visa.search_count(domain)
            rec.quota_remaining = (
                max(0, rec.seasonal_quota - rec.quota_used)
                if rec.seasonal_quota else 0)

    currency_id = fields.Many2one(
        'res.currency',
        string='Currency',
        default=lambda self: self.env.company.currency_id,
    )

    price_line_ids = fields.One2many(
        'umrah.agent.price',
        'agent_id',
        string='Pricing',
    )

    current_sale_price = fields.Monetary(
        string='Current Sale Price',
        compute='_compute_current_prices',
        currency_field='currency_id',
    )
    current_purchase_price = fields.Monetary(
        string='Current Purchase Price',
        compute='_compute_current_prices',
        currency_field='currency_id',
    )

    # --------------------------------------------------
    # Billing type (Section 4.2) - SEPARATE from agent_type.
    # Drives visa invoice construction: 'default' = full 3-line
    # invoice (debtor pays full amount); 'actual' = visa-fees-only.
    # --------------------------------------------------
    agent_billing_type = fields.Selection([
        ('default', 'Default'),
        ('actual', 'Actual'),
    ], string='Billing Type', default='default')

    # --------------------------------------------------
    # Sub-Agents hierarchy (Section 4.3)
    # --------------------------------------------------
    parent_agent_id = fields.Many2one('umrah.agent', string='Parent Agent')
    sub_agent_ids = fields.One2many(
        'umrah.agent', 'parent_agent_id', string='Sub-Agents'
    )

    def _get_active_price(self, date=None):
        """Return the pricing line active on ``date`` (default: today).

        A line is active when ``date`` falls within ``date_from``/``date_to``
        (open-ended bounds are treated as always-valid). When several match,
        the most recent ``date_from`` wins. Falls back to the latest line if
        none bracket the date. Returns an empty recordset if there are none.
        """
        self.ensure_one()
        if not self.price_line_ids:
            return self.env['umrah.agent.price']

        date = date or fields.Date.context_today(self)

        def _matches(line):
            if line.date_from and line.date_from > date:
                return False
            if line.date_to and line.date_to < date:
                return False
            return True

        candidates = self.price_line_ids.filtered(_matches)
        if candidates:
            return candidates.sorted(
                key=lambda l: l.date_from or fields.Date.to_date('1900-01-01'),
                reverse=True,
            )[0]
        # No line brackets the date: fall back to the most recent line.
        return self.price_line_ids.sorted(
            key=lambda l: l.date_from or fields.Date.to_date('1900-01-01'),
            reverse=True,
        )[0]

    @api.depends(
        'price_line_ids',
        'price_line_ids.sale_price',
        'price_line_ids.purchase_price',
        'price_line_ids.date_from',
        'price_line_ids.date_to',
    )
    def _compute_current_prices(self):
        for record in self:
            line = record._get_active_price()
            record.current_sale_price = line.sale_price if line else 0.0
            record.current_purchase_price = line.purchase_price if line else 0.0

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
            if not vals.get('code'):
                vals['code'] = self.env['ir.sequence'].next_by_code('umrah.agent') or 'New'
        records = super(UmrahAgent, self).create(vals_list)
        for agent in records:
            if not agent.partner_id:
                agent.partner_id = agent.create_partner(
                    agent.name, agent.email, agent.phone)
        return records

    @api.depends('trip_ids', 'group_ids')
    def _compute_statistics(self):
        for record in self:
            record.total_trips_organized = len(record.trip_ids)

            total_pilgrims = 0
            for group in record.group_ids:
                total_pilgrims += group.current_members

            record.total_pilgrims_served = total_pilgrims
    
    @api.depends('trip_ids', 'group_ids')
    def _compute_performance_metrics(self):
        for record in self:
            completed_trips = record.trip_ids.filtered(lambda t: t.status == 'completed')
            if record.trip_ids:
                record.success_rate = (len(completed_trips) / len(record.trip_ids)) * 100
            else:
                record.success_rate = 0.0

            if record.group_ids:
                total_members = sum(group.current_members for group in record.group_ids)
                record.average_group_size = total_members / len(record.group_ids)
            else:
                record.average_group_size = 0.0
    
    def action_submit_for_approval(self):
        self.status = 'pending_approval'
    
    def action_approve(self):
        self.status = 'approved'
        self.approval_date = fields.Date.today()
        self.approved_by = self.env.user
    
    def action_suspend(self):
        self.status = 'suspended'
        self.is_active = False
    
    def action_blacklist(self):
        self.status = 'blacklisted'
        self.is_active = False
    
    def action_reactivate(self):
        self.status = 'approved'
        self.is_active = True
    
    @api.constrains('rating', 'customer_satisfaction')
    def _check_ratings(self):
        for record in self:
            if record.rating and (record.rating < 1 or record.rating > 5):
                raise ValidationError("Rating must be between 1 and 5.")
            if record.customer_satisfaction and (record.customer_satisfaction < 1 or record.customer_satisfaction > 5):
                raise ValidationError(_("Customer satisfaction must be between 1 and 5."))

    @api.constrains('commission_rate')
    def _check_commission_rate(self):
        for record in self:
            if record.commission_rate and (record.commission_rate < 0 or record.commission_rate > 100):
                raise ValidationError(_("Commission rate must be between 0 and 100 percent."))



class AccountMove(models.Model):
    _inherit = 'account.move'

    agent_id = fields.Many2one('umrah.agent', string='Agent')

# -*- coding: utf-8 -*-
from odoo import api, models, fields, _
from odoo.exceptions import UserError, ValidationError
from dateutil.relativedelta import relativedelta
import requests
import logging

_logger = logging.getLogger(__name__)

class CrmForsah(models.Model):
    _name = "crm.forsah.client"
    _description = "CRM Forsah Client"
    _inherit = ['mail.thread', 'mail.activity.mixin']
    name = fields.Char(string="Name", required=True, readonly=True, tracking=True)
    category = fields.Char(string="Category", readonly=True, tracking=True)
    link = fields.Char(string="Link", readonly=True)
    size = fields.Char(string="Size", readonly=True, tracking=True)
    days = fields.Char(string="Days", readonly=True, tracking=True)
    city = fields.Char(string="City", readonly=True, tracking=True)
    tag_ids = fields.Many2many(
        'crm.forsah.tag.client',
        'crm_forsah_tag_rel',
        'forsah_id',
        'tag_id',
        string='Tags'
    )
    forsah_id = fields.Char(string="Ref ID", readonly=True, tracking=True)
    active = fields.Boolean(string="Active", default=True, tracking=True)
    company_id = fields.Many2one('res.company', string='Company', default=lambda self: self.env.company)

    def get_forsah_data(self):
        """Fetch and process Forsah data from the API."""
        url = 'https://service.era.net.sa/forsah/data'
        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            data_list = response.json()
            
            if data_list.get('code') != '200':
                error_msg = _(f"Unexpected response code: {data_list.get('code')}")
                _logger.error(error_msg)
                raise UserError(error_msg)
                
            # Delete existing records in a transaction
            with self.env.cr.savepoint():
                self._delete_all_records()
                
                for data in data_list.get('data', []):
                    try:
                        vals = {
                            'forsah_id': data.get('id'),
                            'name': data.get('name'),
                            'link': data.get('link'),
                            'size': data.get('size'),
                            'category': data.get('category'),
                            'days': data.get('days'),
                            'city': data.get('city'),
                        }
                        new_record = self.create(vals)
                        self.process_categories_to_tags()
                        _logger.info(f"Created Forsah record: {new_record.name}")
                    except Exception as e:
                        _logger.error(f"Error processing record: {e}")
                        continue

        except requests.exceptions.RequestException as e:
            error_msg = _(f"API Request Error: {str(e)}")
            _logger.error(error_msg)
            raise UserError(error_msg)
        except ValueError as e:
            error_msg = _("Invalid JSON response from API")
            _logger.error(f"{error_msg}: {str(e)}")
            raise UserError(error_msg)
        except Exception as e:
            error_msg = _(f"Unexpected error: {str(e)}")
            _logger.error(error_msg)
            raise UserError(error_msg)
        
        
    @api.model
    def process_categories_to_tags(self):
        for record in self.search([]):
            if record.category:
                categories = record.category.split(',')
                ids = []
                for category in categories:
                    category = category.strip()
                    if category:
                        tag = self.env['crm.forsah.tag.client'].sudo().search([('name', '=', category)], limit=1)
                        if not tag:
                            tag = self.env['crm.forsah.tag.client'].sudo().create({'name': category})
                        ids.append(tag.id)
                        record.tag_ids = [(6, 0, ids)]

    @api.constrains('name')
    def _check_name(self):
        for record in self:
            if not record.name:
                raise ValidationError("The Name field cannot be empty.")
    @api.model
    def _delete_all_records(self):
        records = self.search([])
        records.unlink()
       
    def forsah_open_link(self):
            for record in self:
                dynamic_url = f"{record.link}"  
                return {
                        'type': 'ir.actions.act_url',
                        'url': dynamic_url,
                        'target': 'new',}

    def _get_source(self):
            source =self.env['utm.source'].search([('name','=','Forsah')])
            if len(source)==0:
                return False   
            else:
                return source.id  
                             
    def action_create_lead(self):
        """Create a CRM lead from Forsah data."""
        self.ensure_one()
        Lead = self.env['crm.lead']
        
        # Check for existing lead
        existing_lead = Lead.search([
            ('name', '=', self.name),
            ('description', 'ilike', self.category)
        ], limit=1)
        
        if existing_lead:
            raise ValidationError(_("A lead has already been created for this opportunity."))
            
        try:
            # Get or create UTM source
            source_id = self._get_source() or self.env['utm.source'].create({
                'name': "Forsah"
            }).id
            
            # Prepare lead data
            description = f"{self.category} | Days {self.days} | Size {self.size}"
            lead_data = {
                'name': self.name,
                'description': description,
                'type': 'opportunity',
                'team_id': self.env['crm.team'].search([], limit=1).id,
                'city': self.city,
                'website': self.link,
                'source_id': source_id,
                'company_id': self.company_id.id,
            }
            
            # Create lead with activity in a transaction
            with self.env.cr.savepoint():
                lead = Lead.create(lead_data)
                
                # Create activity
                activity = self.env['mail.activity'].create({
                    'display_name': lead.name,
                    'summary': _('Review Opportunity'),
                    'note': _('New Forsah opportunity requires review within 3 days.'),
                    'user_id': self.env.user.id,
                    'res_id': lead.id,
                    'res_model_id': self.env['ir.model']._get('crm.lead').id,
                    'activity_type_id': self.env.ref('mail.mail_activity_data_todo').id,
                    'date_deadline': fields.Date.context_today(self) + relativedelta(days=3),
                })
                
                _logger.info(f"Created lead and activity for Forsah: {self.name}")
                return activity
                
        except Exception as e:
            error_msg = _(f"Failed to create lead: {str(e)}")
            _logger.error(error_msg)
            raise UserError(error_msg)


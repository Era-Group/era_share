# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError, UserError


class WhatsAppTemplateVariable(models.Model):
    _name = 'sadeem.waha.whatsapp.template.variable'
    _description = 'WhatsApp Template Variable'
    _order = 'sequence, id'

    name = fields.Char('Variable Name', required=True, help='e.g., {{1}}, {{2}}')
    sequence = fields.Integer('Sequence', default=10)
    template_id = fields.Many2one('sadeem.waha.whatsapp.template', 'Template', required=True, ondelete='cascade')

    field_type = fields.Selection([
        ('field', 'Field of Model'),
        ('free_text', 'Free Text'),
        ('user_name', 'User Name'),
        ('user_mobile', 'User Mobile'),
        ('user_company', 'User Company'),
    ], string='Type', default='field', required=True)
    model_id = fields.Many2one('ir.model', string='Applies to', related='template_id.model_id')
    field_name = fields.Many2one('ir.model.fields', string='Field', domain='[("model_id", "=", model_id)]')
    demo_value = fields.Char('Sample Value', default='Sample Value')

    def get_value(self, record):
        """Get the value of this variable for the given record"""
        self.ensure_one()

        if self.field_type == 'user_name':
            return self.env.user.name
        elif self.field_type == 'user_mobile':
            return self.env.user.mobile or ''
        elif self.field_type == 'user_company':
            return self.env.user.company_id and self.env.user.company_id.name or ''
        elif self.field_type == 'free_text':
            return self.demo_value or ''
        elif self.field_type == 'field' and self.field_name:
            try:
                # sudo() needed: regular users lack ACL access to ir.model.fields
                field_path = self.field_name.sudo().name
                value = record
                for field in field_path.split('.'):
                    if hasattr(value, field):
                        value = getattr(value, field)
                    else:
                        return self.demo_value or ''

                # Convert to string
                if value:
                    if isinstance(value, (list, tuple)):
                        return ', '.join([str(v) for v in value])
                    return str(value)
                return self.demo_value or ''
            except Exception:
                return self.demo_value or ''

        return self.demo_value or ''

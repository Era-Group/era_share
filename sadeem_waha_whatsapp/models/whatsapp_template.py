# -*- coding: utf-8 -*-
from odoo import models, fields, api
import re


class WhatsAppTemplate(models.Model):
    _name = 'sadeem.waha.whatsapp.template'
    _description = 'WhatsApp Message Template'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char('Template Name', required=True, tracking=True)
    message_text = fields.Text('Message Text', required=True, tracking=True,
                               help='Use {{1}}, {{2}}, etc. for variables')
    template_type = fields.Selection([
        ('text', 'Text Only'),
        ('with_attachment', 'With Attachment')
    ], string='Type', default='text', tracking=True)

    attachment_id = fields.Many2one('ir.attachment', 'Default Attachment')
    active = fields.Boolean('Active', default=True)
    company_id = fields.Many2one('res.company', 'Company', default=lambda self: self.env.company, tracking=True)

    # Applies to
    model_id = fields.Many2one('ir.model', 'Applies to',
                               help='Select the model this template applies to')
    model = fields.Char('Model Name', related='model_id.model', store=True, readonly=True)

    # Variables
    variable_ids = fields.One2many('sadeem.waha.whatsapp.template.variable', 'template_id',
                                   string='Variables', copy=True)

    @api.onchange('message_text')
    def _onchange_message_text(self):
        """Auto-create variables when message text changes"""
        if self.message_text:
            # Find all {{number}} patterns
            pattern = r'{{(\d+)}}'
            matches = re.findall(pattern, self.message_text)

            if matches:
                # Get unique variable numbers
                variable_numbers = sorted(set([int(m) for m in matches]))

                # Get existing variables
                existing_vars = {v.name: v for v in self.variable_ids}

                # Create new variables if needed
                new_variables = []
                for num in variable_numbers:
                    var_name = f'{{{{{num}}}}}'
                    if var_name not in existing_vars:
                        new_variables.append((0, 0, {
                            'name': var_name,
                            'sequence': num,
                            'field_type': 'free_text',
                            'demo_value': f'Value {num}',
                        }))

                if new_variables:
                    self.variable_ids = new_variables

    def get_filled_message(self, record=None):
        """Get message text with variables filled from record"""
        self.ensure_one()
        message = self.message_text

        if not record:
            # Use demo values
            for variable in self.variable_ids:
                message = message.replace(variable.name, variable.demo_value or '')
        else:
            # Use actual values from record
            for variable in self.variable_ids:
                value = variable.get_value(record)
                message = message.replace(variable.name, value)

        return message

    def action_use_template(self):
        """Use this template to create a new message"""
        return {
            'type': 'ir.actions.act_window',
            'name': 'Send WhatsApp Message',
            'res_model': 'sadeem.waha.send.whatsapp.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_message_text': self.message_text,
                'default_template_id': self.id,
                'default_attachment_id': self.attachment_id.id if self.attachment_id else False,
                'default_session_id': self.company_id.default_whatsapp_session_id and self.company_id.default_whatsapp_session_id.id or False,
            }
        }

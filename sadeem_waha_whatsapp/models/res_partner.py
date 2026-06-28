# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import UserError
import re


class ResPartner(models.Model):
    _inherit = 'res.partner'

    waha_whatsapp_number = fields.Char(
        'WhatsApp Number',
        compute='_compute_waha_whatsapp_number',
        store=True,
        help="WhatsApp phone number (auto-computed from phone)"
    )
    waha_whatsapp_message_ids = fields.One2many(
        'sadeem.waha.whatsapp.message',
        'partner_id',
        'WhatsApp Messages',
        groups='sadeem_waha_whatsapp.group_waha_whatsapp_user',
    )
    waha_whatsapp_message_count = fields.Integer(
        'WhatsApp Message Count',
        compute='_compute_waha_whatsapp_message_count',
        groups='sadeem_waha_whatsapp.group_waha_whatsapp_user',
    )

    @api.depends('phone')
    def _compute_waha_whatsapp_number(self):
        """Compute WhatsApp number from phone, removing spaces and formatting"""
        for partner in self:
            number = partner.phone or ''
            if number:
                # Remove spaces, dashes, parentheses, and other formatting
                cleaned_number = re.sub(r'[\s\-\(\)\.]', '', number)

                # Ensure number starts with + for international format
                # If it doesn't, leave it as is but user will get error when sending
                if cleaned_number and not cleaned_number.startswith('+'):
                    # Try to detect and warn, but don't modify
                    # User should fix the mobile/phone field to include country code
                    partner.waha_whatsapp_number = cleaned_number
                else:
                    partner.waha_whatsapp_number = cleaned_number
            else:
                partner.waha_whatsapp_number = False

    @api.depends('waha_whatsapp_message_ids')
    def _compute_waha_whatsapp_message_count(self):
        for partner in self:
            partner.waha_whatsapp_message_count = len(partner.waha_whatsapp_message_ids)

    def action_send_whatsapp(self):
        """Open wizard to send WhatsApp message"""
        self.ensure_one()

        if not self.waha_whatsapp_number:
            raise UserError(_("No WhatsApp number available for this contact. Please add a phone number."))

        return {
            'type': 'ir.actions.act_window',
            'name': 'Send WhatsApp Message',
            'res_model': 'sadeem.waha.send.whatsapp.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_partner_id': self.id,
                'default_phone_number': self.waha_whatsapp_number,
            }
        }

    def action_view_whatsapp_messages(self):
        """View WhatsApp messages for this partner"""
        self.ensure_one()

        return {
            'type': 'ir.actions.act_window',
            'name': 'WhatsApp Messages',
            'res_model': 'sadeem.waha.whatsapp.message',
            'view_mode': 'list,form',
            'domain': [('partner_id', '=', self.id)],
            'context': {'default_partner_id': self.id}
        }

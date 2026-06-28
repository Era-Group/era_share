# -*- coding: utf-8 -*-
from odoo import models, fields


class ResCompany(models.Model):
    _inherit = 'res.company'

    default_whatsapp_session_id = fields.Many2one(
        'sadeem.waha.session',
        string='Default WhatsApp Session',
        help="Default WhatsApp session for this company"
    )

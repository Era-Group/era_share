# -*- coding: utf-8 -*-
# Copyright 2020 CorTex IT Solutions Ltd. (<https://cortexsolutions.net/>)
# License OPL-1

from odoo import models, fields, _, api


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    property_deferred_account_revenue_id = fields.Many2one('account.account', company_dependent=True, tracking=True, ondelete='restrict', string="Deferred Revenue Account")
    property_deferred_account_expense_id = fields.Many2one('account.account', company_dependent=True, tracking=True, ondelete='restrict', string="Deferred Expense Account")

# -*- coding: utf-8 -*-
# Copyright 2020 CorTex IT Solutions Ltd. (<https://cortexsolutions.net/>)
# License OPL-1

from odoo import models, fields, _, api, Command
from odoo.exceptions import UserError
from dateutil.relativedelta import relativedelta


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    deferred_account_id = fields.Many2one(comodel_name='account.account',string="Deferred Account")

    # On change product change the deferred account
    @api.onchange('product_id')
    def _onchange_product_id(self):
        if self.product_id:
            if self.move_id.is_sale_document(include_receipts=True):
                self.deferred_account_id = (self.product_id.product_tmpl_id.property_deferred_account_revenue_id
                                            and self.product_id.product_tmpl_id.property_deferred_account_revenue_id.id
                                            or False)
            elif self.move_id.is_purchase_document(include_receipts=True):
                self.deferred_account_id = (self.product_id.product_tmpl_id.property_deferred_account_expense_id
                                            and self.product_id.product_tmpl_id.property_deferred_account_expense_id.id
                                            or False)
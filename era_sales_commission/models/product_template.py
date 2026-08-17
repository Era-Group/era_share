from odoo import fields, models


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    commission_rate_per_unit = fields.Monetary(
        string='Commission per Unit', currency_field='currency_id',
        help="What one unit of this product earns on a quantity commission. It "
             "is the third source consulted: a unit price tier of the product "
             "wins over it, and a unit price set on the agent wins over that.")

from odoo import api, fields, models
from odoo.exceptions import ValidationError


class EraCommissionUnitPriceTier(models.Model):
    """What one unit of a product earns, by how many of it were sold.

    The tier is chosen on the **net** quantity of the product over the period --
    what was sold less what came back -- which is why a quantity commission is
    one line per product and per period rather than one line per invoice line:
    the slice cannot be known before the period is added up.
    """

    _name = 'era.commission.unit.price.tier'
    _description = 'Commission Unit Price Tier'
    _order = 'product_id, qty_from, id'
    _rec_name = 'product_id'

    product_id = fields.Many2one(
        'product.product', string='Product', required=True,
        ondelete='cascade', index=True)
    qty_from = fields.Float(
        string='From Quantity', digits='Product Unit',
        help="Net quantity of the period from which this unit price applies.")
    qty_to = fields.Float(
        string='To Quantity', digits='Product Unit',
        help="Leave at zero for an open-ended top tier.")
    unit_price = fields.Monetary(
        string='Unit Price', required=True, currency_field='currency_id')
    company_id = fields.Many2one(
        'res.company', string='Company', required=True, index=True,
        default=lambda self: self.env.company)
    currency_id = fields.Many2one(
        related='company_id.currency_id', string='Currency')

    @api.depends('product_id', 'qty_from', 'qty_to', 'unit_price')
    def _compute_display_name(self):
        for tier in self:
            upper = f'{tier.qty_to:g}' if tier.qty_to else '∞'
            tier.display_name = (
                f'{tier.product_id.display_name or "-"} '
                f'[{tier.qty_from:g} - {upper}] → {tier.unit_price:g}')

    @api.constrains('qty_from', 'qty_to', 'unit_price', 'product_id', 'company_id')
    def _check_range(self):
        for tier in self:
            if tier.qty_from < 0 or tier.unit_price < 0:
                raise ValidationError(tier.env._(
                    "A quantity threshold and a unit price cannot be negative."))
            if tier.qty_to and tier.qty_to < tier.qty_from:
                raise ValidationError(tier.env._(
                    "The tier of %(product)s ends before it starts.",
                    product=tier.product_id.display_name))
            others = self.search([
                ('id', '!=', tier.id),
                ('product_id', '=', tier.product_id.id),
                ('company_id', '=', tier.company_id.id),
            ])
            for other in others:
                starts_after = other.qty_to and tier.qty_from > other.qty_to
                ends_before = tier.qty_to and tier.qty_to < other.qty_from
                if not starts_after and not ends_before:
                    raise ValidationError(tier.env._(
                        "Two unit price tiers of %(product)s overlap. One "
                        "quantity has to name one price.",
                        product=tier.product_id.display_name))

    @api.model
    def _price_for(self, product, quantity):
        """The unit price the net quantity of the period falls into, or zero."""
        if not product:
            return 0.0
        tiers = self.search([
            ('product_id', '=', product.id),
            ('company_id', 'in', self.env.companies.ids),
        ], order='qty_from asc')
        for tier in tiers:
            if quantity >= tier.qty_from and (not tier.qty_to or quantity <= tier.qty_to):
                return tier.unit_price
        return 0.0

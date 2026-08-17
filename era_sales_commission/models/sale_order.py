from odoo import api, fields, models


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    era_agent_id = fields.Many2one(
        'era.commission.agent', string='Commission Agent',
        compute='_compute_era_agent_id', store=True, readonly=False,
        precompute=True, index='btree_not_null', ondelete='restrict',
        help="Who earns the commission on this order. Filled from the customer, "
             "or from the salesperson, and can be overridden by hand.")
    era_agent_share_ids = fields.One2many(
        'era.commission.share', 'order_id', string='Commission Split',
        help="Leave empty to credit the whole order to its commission agent.")
    era_commission_line_ids = fields.One2many(
        'era.commission.line', 'sale_order_id', string='Commission Lines')
    era_commission_currency_id = fields.Many2one(
        related='company_id.currency_id', string='Company Currency',
        help="Commissions are always kept in the company currency; a sales order "
             "is not.")
    era_commission_amount = fields.Monetary(
        string='Commission', compute='_compute_era_commission',
        currency_field='era_commission_currency_id')
    era_commission_count = fields.Integer(
        string='# Commission Lines', compute='_compute_era_commission')

    @api.depends('partner_id', 'user_id')
    def _compute_era_agent_id(self):
        # sudo: a salesperson is allowed to sell without being allowed to read
        # the commission book of the company.
        Agent = self.env['era.commission.agent'].sudo()
        for order in self:
            if order.era_agent_id:
                continue
            order.era_agent_id = Agent._agent_for(order.partner_id, order.user_id)

    @api.depends('era_commission_line_ids.commission_amount')
    def _compute_era_commission(self):
        for order in self:
            lines = order.sudo().era_commission_line_ids.filtered(
                lambda line: line.state != 'cancel')
            order.era_commission_amount = sum(lines.mapped('commission_amount'))
            order.era_commission_count = len(lines)

    def _prepare_invoice(self):
        """Carry the agent onto the invoice: what is invoiced was sold by someone."""
        vals = super()._prepare_invoice()
        if self.era_agent_id:
            vals['era_agent_id'] = self.era_agent_id.id
        if self.era_agent_share_ids:
            vals['era_agent_share_ids'] = [
                (0, 0, {'agent_id': split.agent_id.id, 'share': split.share,
                        'company_id': split.company_id.id})
                for split in self.era_agent_share_ids
            ]
        return vals

    def action_view_era_commission(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._('Commission Lines'),
            'res_model': 'era.commission.line',
            'view_mode': 'list,form',
            'domain': [('sale_order_id', '=', self.id)],
        }

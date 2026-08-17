from odoo import api, fields, models


class AccountMove(models.Model):
    _inherit = 'account.move'

    era_agent_id = fields.Many2one(
        'era.commission.agent', string='Commission Agent',
        compute='_compute_era_agent_id', store=True, readonly=False,
        index='btree_not_null', ondelete='restrict',
        help="Who earns the commission on this invoice. Copied from the sales "
             "order, or read from the customer or the salesperson.")
    era_agent_share_ids = fields.One2many(
        'era.commission.share', 'move_id', string='Commission Split')
    era_commission_line_ids = fields.One2many(
        'era.commission.line', 'move_id', string='Commission Lines')
    era_commission_amount = fields.Monetary(
        string='Commission', compute='_compute_era_commission',
        currency_field='company_currency_id')
    era_commission_count = fields.Integer(
        string='# Commission Lines', compute='_compute_era_commission')

    @api.depends('partner_id', 'invoice_user_id', 'move_type')
    def _compute_era_agent_id(self):
        # sudo: see sale.order._compute_era_agent_id
        Agent = self.env['era.commission.agent'].sudo()
        for move in self:
            if move.era_agent_id or move.move_type not in ('out_invoice', 'out_refund'):
                continue
            move.era_agent_id = Agent._agent_for(
                move.partner_id, move.invoice_user_id)

    @api.depends('era_commission_line_ids.commission_amount')
    def _compute_era_commission(self):
        for move in self:
            lines = move.sudo().era_commission_line_ids.filtered(
                lambda line: line.state != 'cancel')
            move.era_commission_amount = sum(lines.mapped('commission_amount'))
            move.era_commission_count = len(lines)

    def action_view_era_commission(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._('Commission Lines'),
            'res_model': 'era.commission.line',
            'view_mode': 'list,form',
            'domain': [('move_id', '=', self.id)],
        }

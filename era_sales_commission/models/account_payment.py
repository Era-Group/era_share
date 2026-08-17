from odoo import api, fields, models


class AccountPayment(models.Model):
    """The agent credited with bringing the money in.

    Filled from the customer, or from whoever recorded the payment, and can be
    overridden by hand. On a commission earned on collection it is this agent --
    not the one on the invoice -- who is paid, which is how a rep who collects
    someone else's invoice gets credited for it.
    """

    _inherit = 'account.payment'

    era_agent_id = fields.Many2one(
        'era.commission.agent', string='Commission Agent',
        compute='_compute_era_agent_id', store=True, readonly=False,
        index='btree_not_null', ondelete='restrict',
        help="Who earns the commission on the money this payment brought in. "
             "Leave it as it is to credit the agent of the invoice.")
    era_commission_line_ids = fields.One2many(
        'era.commission.line', 'payment_id', string='Commission Lines')
    era_commission_amount = fields.Monetary(
        string='Commission', compute='_compute_era_commission',
        currency_field='company_currency_id')
    era_commission_count = fields.Integer(
        string='# Commission Lines', compute='_compute_era_commission')

    @api.depends('partner_id', 'partner_type')
    def _compute_era_agent_id(self):
        # sudo: recording a customer payment must not require the right to read
        # the commission book of the company. See sale.order._compute_era_agent_id.
        Agent = self.env['era.commission.agent'].sudo()
        for payment in self:
            if payment.era_agent_id or payment.partner_type != 'customer':
                continue
            payment.era_agent_id = Agent._agent_for(
                payment.partner_id, payment.create_uid or self.env.user)

    @api.depends('era_commission_line_ids.commission_amount')
    def _compute_era_commission(self):
        for payment in self:
            lines = payment.sudo().era_commission_line_ids.filtered(
                lambda line: line.state != 'cancel')
            payment.era_commission_amount = sum(
                lines.mapped('commission_amount'))
            payment.era_commission_count = len(lines)

    def action_view_era_commission(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._('Commission Lines'),
            'res_model': 'era.commission.line',
            'view_mode': 'list,form',
            'domain': [('payment_id', '=', self.id)],
        }

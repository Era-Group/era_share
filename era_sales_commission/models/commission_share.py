from odoo import api, fields, models
from odoo.exceptions import ValidationError


class EraCommissionShare(models.Model):
    """Two or more agents on one deal, each with their part of it.

    Left empty, the whole document goes to its single commission agent. As soon
    as one split exists, the splits are the truth and the agent field on the
    document is ignored.
    """

    _name = 'era.commission.share'
    _description = 'Commission Share'
    _order = 'id'
    _rec_name = 'agent_id'

    order_id = fields.Many2one(
        'sale.order', string='Sales Order', ondelete='cascade',
        index='btree_not_null')
    move_id = fields.Many2one(
        'account.move', string='Invoice', ondelete='cascade',
        index='btree_not_null')
    agent_id = fields.Many2one(
        'era.commission.agent', string='Agent', required=True,
        ondelete='restrict')
    share = fields.Float(
        string='Share (%)', required=True, default=100.0, digits='Discount')
    company_id = fields.Many2one(
        'res.company', string='Company', required=True,
        default=lambda self: self.env.company)

    @api.constrains('share', 'order_id', 'move_id')
    def _check_share(self):
        for split in self:
            if split.share <= 0:
                raise ValidationError(split.env._(
                    "A share has to be a positive percentage."))
            document = split.order_id or split.move_id
            if not document:
                raise ValidationError(split.env._(
                    "A commission share belongs to a sales order or an invoice."))
            total = sum(document.era_agent_share_ids.mapped('share'))
            if total > 100.0:
                raise ValidationError(split.env._(
                    "The commission shares of %(name)s add up to %(total)s%%. "
                    "They cannot exceed 100.",
                    name=document.display_name, total=round(total, 2)))

    _agent_uniq_order = models.Constraint(
        'unique(order_id, agent_id)',
        'An agent appears once per sales order.')
    _agent_uniq_move = models.Constraint(
        'unique(move_id, agent_id)',
        'An agent appears once per invoice.')

from odoo import fields, models


class ResPartner(models.Model):
    _inherit = 'res.partner'

    era_agent_id = fields.Many2one(
        'era.commission.agent', string='Commission Agent',
        help="The agent who earns the commission on this customer's orders, "
             "whoever encoded them.")

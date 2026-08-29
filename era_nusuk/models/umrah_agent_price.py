# -*- coding: utf-8 -*-

from odoo import models, fields


class UmrahAgentPrice(models.Model):
    _name = "umrah.agent.price"
    _description = "Agent Pricing Line"
    _order = "date_from desc, id desc"

    agent_id = fields.Many2one(
        "umrah.agent",
        string="Agent",
        required=True,
        ondelete="cascade",
    )
    sale_price = fields.Monetary(
        string="Sale Price",
        currency_field="currency_id",
    )
    purchase_price = fields.Monetary(
        string="Purchase Price",
        currency_field="currency_id",
    )
    currency_id = fields.Many2one(
        "res.currency",
        string="Currency",
        default=lambda self: self.env.company.currency_id,
    )
    date_from = fields.Date(string="Valid From")
    date_to = fields.Date(string="Valid To")

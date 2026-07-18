# -*- coding: utf-8 -*-
from odoo import api, fields, models, _


class ResPartner(models.Model):
    _inherit = 'res.partner'

    cs_account_id = fields.Many2one(
        'cs.account', string='Customer Success Account', copy=False,
        help="The Customer Success account managing this customer.")
    is_cs_managed = fields.Boolean(
        string='Managed by Customer Success', compute='_compute_is_cs_managed',
        store=True)
    csm_user_id = fields.Many2one(
        'res.users', related='cs_account_id.csm_user_id', string='CSM Engineer',
        store=True, readonly=True)

    @api.depends('cs_account_id')
    def _compute_is_cs_managed(self):
        for partner in self:
            partner.is_cs_managed = bool(partner.cs_account_id)

    def action_open_cs_account(self):
        self.ensure_one()
        account = self.cs_account_id or self.commercial_partner_id.cs_account_id
        if not account:
            return self.action_create_cs_account()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Customer Success'),
            'res_model': 'cs.account',
            'res_id': account.id,
            'view_mode': 'form',
        }

    def action_create_cs_account(self):
        self.ensure_one()
        commercial = self.commercial_partner_id or self
        account = self.env['cs.account'].search([('partner_id', '=', commercial.id)], limit=1)
        if not account:
            account = self.env['cs.account'].create({
                'partner_id': commercial.id,
                'csm_user_id': self.user_id.id if self.user_id else False,
            })
        return {
            'type': 'ir.actions.act_window',
            'name': _('Customer Success'),
            'res_model': 'cs.account',
            'res_id': account.id,
            'view_mode': 'form',
        }

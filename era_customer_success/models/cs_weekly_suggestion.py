# -*- coding: utf-8 -*-
from odoo import api, fields, models, _


class CsWeeklySuggestion(models.Model):
    _name = 'cs.weekly.suggestion'
    _description = 'Weekly Customer Success Suggestion'
    _order = 'week desc, rank, id'
    _rec_name = 'partner_id'

    cs_account_id = fields.Many2one(
        'cs.account', string='Account', required=True, ondelete='cascade', index=True)
    partner_id = fields.Many2one(
        related='cs_account_id.partner_id', store=True, string='Customer')
    csm_user_id = fields.Many2one(
        related='cs_account_id.csm_user_id', store=True, index=True, string='CSM Engineer')
    company_id = fields.Many2one(
        related='cs_account_id.company_id', store=True)
    week = fields.Date(string='Week of', required=True, index=True)
    rank = fields.Integer(string='Priority Order', default=10)
    priority = fields.Selection([
        ('urgent', 'Urgent'),
        ('high', 'High'),
        ('medium', 'Medium'),
        ('low', 'Low'),
    ], string='Priority', default='medium', required=True)
    reason = fields.Text(string='Why')
    recommended_action = fields.Text(string='Guidance')   # التوجيه
    health_score = fields.Integer(string='Health', readonly=True)
    churn_probability = fields.Integer(string='Churn 90d (%)', readonly=True)
    state = fields.Selection([
        ('open', 'To Do'),
        ('done', 'Done'),
        ('dismissed', 'Dismissed'),
    ], string='Status', default='open', required=True)
    generated_on = fields.Datetime(string='Generated On', readonly=True)

    def action_open_account(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.partner_id.display_name,
            'res_model': 'cs.account',
            'res_id': self.cs_account_id.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_mark_done(self):
        self.write({'state': 'done'})

    def action_dismiss(self):
        self.write({'state': 'dismissed'})

    def action_reopen(self):
        self.write({'state': 'open'})

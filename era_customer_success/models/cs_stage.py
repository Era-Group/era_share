# -*- coding: utf-8 -*-
from odoo import api, fields, models


class CsStage(models.Model):
    _name = 'cs.stage'
    _description = 'Customer Success Lifecycle Stage'
    _order = 'sequence, id'

    name = fields.Char(string='Stage', required=True, translate=True)
    sequence = fields.Integer(default=10)
    fold = fields.Boolean(
        string='Folded in Kanban',
        help="Folded stages (e.g. Churned) are collapsed by default in the Kanban view.")
    color = fields.Integer(string='Color')
    description = fields.Text(translate=True)

    is_onboarding = fields.Boolean(
        string='Onboarding Stage',
        help="Marks a stage that belongs to the 0-90 days onboarding journey. "
             "The lifecycle cron advances onboarding accounts through these stages.")
    is_at_risk = fields.Boolean(string='At-Risk Stage')
    is_churned = fields.Boolean(string='Churned Stage')

    day_window_start = fields.Integer(
        string='From Day',
        help="Start of the onboarding day window (relative to onboarding start date).")
    day_window_end = fields.Integer(
        string='To Day',
        help="End of the onboarding day window. The lifecycle cron promotes the "
             "account to the next onboarding stage once this many days have elapsed.")
    mail_template_id = fields.Many2one(
        'mail.template', string='Email on Entry',
        domain="[('model', '=', 'cs.account')]",
        help="Optional email automatically sent to the customer when an account "
             "enters this stage.")

    account_count = fields.Integer(string='# Accounts', compute='_compute_account_count')

    def _compute_account_count(self):
        data = self.env['cs.account']._read_group(
            [('lifecycle_stage_id', 'in', self.ids)],
            groupby=['lifecycle_stage_id'], aggregates=['__count'])
        mapped = {stage.id: count for stage, count in data}
        for stage in self:
            stage.account_count = mapped.get(stage.id, 0)

# -*- coding: utf-8 -*-
from datetime import timedelta

from odoo import api, fields, models, _
from odoo.exceptions import UserError


class CsWeeklySuggestion(models.Model):
    _name = 'cs.weekly.suggestion'
    _description = 'Weekly Customer Success Suggestion'
    _order = 'state, due_date, rank, id'
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
    source = fields.Selection([
        ('automation', 'Automation'),
        ('ai', 'AI'),
        ('manual', 'Manual'),
    ], string='Source', default='automation', required=True, readonly=True)
    action_type = fields.Selection([
        ('risk_recovery', 'Risk Recovery'),
        ('support_recovery', 'Support Recovery'),
        ('relationship', 'Relationship Follow-up'),
        ('renewal', 'Renewal Follow-up'),
        ('value', 'Deliver Value'),
        ('growth', 'Explore Need'),
    ], string='Work Type', default='relationship', required=True)
    due_date = fields.Date(string='Due Date', required=True, default=fields.Date.context_today, index=True)
    health_score = fields.Integer(string='Health', readonly=True)
    churn_probability = fields.Integer(string='Churn 90d (%)', readonly=True)
    state = fields.Selection([
        ('open', 'To Do'),
        ('done', 'Done'),
        ('dismissed', 'Dismissed'),
    ], string='Status', default='open', required=True)
    generated_on = fields.Datetime(string='Generated On', readonly=True)
    outcome = fields.Selection([
        ('customer_contacted', 'Customer Contacted'),
        ('issue_resolved', 'Issue Resolved'),
        ('value_delivered', 'Value Delivered'),
        ('need_discovered', 'Need Discovered'),
        ('followup_required', 'Follow-up Required'),
        ('no_response', 'No Response'),
        ('not_relevant', 'Not Relevant'),
    ], string='Outcome', readonly=True, copy=False)
    outcome_note = fields.Text(string='Outcome Note', readonly=True, copy=False)
    completed_on = fields.Datetime(string='Completed On', readonly=True, copy=False)
    completed_by_id = fields.Many2one(
        'res.users', string='Completed By', readonly=True, copy=False)
    next_step = fields.Char(string='Next Step', readonly=True, copy=False)
    next_step_date = fields.Date(string='Next Step Date', readonly=True, copy=False)
    is_overdue = fields.Boolean(string='Overdue', compute='_compute_is_overdue', search='_search_is_overdue')

    @api.depends('state', 'due_date')
    def _compute_is_overdue(self):
        today = fields.Date.context_today(self)
        for suggestion in self:
            suggestion.is_overdue = (
                suggestion.state == 'open'
                and bool(suggestion.due_date)
                and suggestion.due_date < today
            )

    def _search_is_overdue(self, operator, value):
        expected = (operator == '=' and value) or (operator == '!=' and not value)
        overdue_domain = [
            ('state', '=', 'open'),
            ('due_date', '<', fields.Date.context_today(self)),
        ]
        return overdue_domain if expected else ['!'] + overdue_domain

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
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Record Outcome'),
            'res_model': 'cs.suggestion.complete',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_suggestion_id': self.id},
        }

    def action_dismiss(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Dismiss Work Item'),
            'res_model': 'cs.suggestion.complete',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_suggestion_id': self.id,
                'default_dismiss': True,
                'default_outcome': 'not_relevant',
            },
        }

    def action_reopen(self):
        self.write({
            'state': 'open',
            'outcome': False,
            'outcome_note': False,
            'completed_on': False,
            'completed_by_id': False,
            'next_step': False,
            'next_step_date': False,
        })

    @api.model
    def _week_start(self, day=None):
        day = day or fields.Date.context_today(self)
        return day - timedelta(days=day.weekday())

    @api.model
    def _upsert_automated_item(self, account, values, week=None):
        week = week or self._week_start()
        item = self.sudo().search([
            ('cs_account_id', '=', account.id),
            ('week', '=', week),
        ], order='state, id desc', limit=1)
        if item and item.state != 'open':
            return item
        vals = {
            **values,
            'cs_account_id': account.id,
            'week': week,
            'health_score': account.health_score,
            'churn_probability': account.churn_probability,
            'generated_on': fields.Datetime.now(),
        }
        if item:
            item.write(vals)
            return item
        return self.sudo().create(vals)

    @api.model
    def _cron_build_daily_worklist(self):
        """Build a dependable worklist from live signals without requiring AI."""
        today = fields.Date.context_today(self)
        week = self._week_start(today)
        stale = self.sudo().search([
            ('state', '=', 'open'),
            ('week', '<', week),
        ])
        if stale:
            stale.write({
                'state': 'dismissed',
                'outcome': 'not_relevant',
                'outcome_note': _('Superseded automatically by the current worklist.'),
                'completed_on': fields.Datetime.now(),
                'completed_by_id': self.env.ref('base.user_root').id,
            })
        accounts = self.env['cs.account'].sudo().search([
            ('csm_user_id', '!=', False),
            ('lifecycle_stage_id.is_churned', '=', False),
        ])
        for account in accounts:
            values = account._daily_work_item_values(today)
            if values:
                self._upsert_automated_item(account, values, week=week)
        return True


class CsSuggestionComplete(models.TransientModel):
    _name = 'cs.suggestion.complete'
    _description = 'Complete Customer Success Work Item'

    suggestion_id = fields.Many2one(
        'cs.weekly.suggestion', required=True, ondelete='cascade', readonly=True)
    partner_id = fields.Many2one(related='suggestion_id.partner_id', readonly=True)
    recommended_action = fields.Text(related='suggestion_id.recommended_action', readonly=True)
    dismiss = fields.Boolean(default=False)
    outcome = fields.Selection([
        ('customer_contacted', 'Customer Contacted'),
        ('issue_resolved', 'Issue Resolved'),
        ('value_delivered', 'Value Delivered'),
        ('need_discovered', 'Need Discovered'),
        ('followup_required', 'Follow-up Required'),
        ('no_response', 'No Response'),
        ('not_relevant', 'Not Relevant'),
    ], string='Outcome', required=True)
    outcome_note = fields.Text(string='What Happened?', required=True)
    next_step = fields.Char(string='Next Step')
    next_step_date = fields.Date(string='Next Step Date')
    schedule_activity = fields.Boolean(
        string='Schedule Follow-up Automatically', default=True)

    @api.constrains('next_step', 'next_step_date')
    def _check_next_step(self):
        for wizard in self:
            if wizard.next_step and not wizard.next_step_date:
                raise UserError(_('Set a date for the next step.'))
            if wizard.next_step_date and not wizard.next_step:
                raise UserError(_('Describe the next step.'))

    def action_confirm(self):
        self.ensure_one()
        suggestion = self.suggestion_id
        state = 'dismissed' if self.dismiss else 'done'
        suggestion.write({
            'state': state,
            'outcome': self.outcome,
            'outcome_note': self.outcome_note,
            'completed_on': fields.Datetime.now(),
            'completed_by_id': self.env.user.id,
            'next_step': self.next_step or False,
            'next_step_date': self.next_step_date or False,
        })
        if self.schedule_activity and self.next_step and self.next_step_date:
            account = suggestion.cs_account_id
            existing = account.activity_ids.filtered(
                lambda activity: activity.user_id == account.csm_user_id
                and activity.date_deadline == self.next_step_date
                and activity.summary == self.next_step)
            if not existing:
                account.activity_schedule(
                    'mail.mail_activity_data_todo',
                    user_id=account.csm_user_id.id,
                    date_deadline=self.next_step_date,
                    summary=self.next_step,
                    note=self.outcome_note,
                )
        return {'type': 'ir.actions.act_window_close'}

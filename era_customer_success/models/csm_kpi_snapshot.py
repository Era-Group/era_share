# -*- coding: utf-8 -*-
from dateutil.relativedelta import relativedelta

from odoo import api, fields, models


class CsmKpiSnapshot(models.Model):
    _name = 'csm.kpi.snapshot'
    _description = 'Customer Success KPI Snapshot'
    _order = 'period_start desc, csm_user_id'
    _rec_name = 'period_start'

    period_start = fields.Date(string='Period', required=True, index=True)
    period_type = fields.Selection([
        ('week', 'Weekly'),
        ('month', 'Monthly'),
        ('quarter', 'Quarterly'),
    ], default='month', required=True)
    cs_account_id = fields.Many2one('cs.account', string='Account', ondelete='cascade', index=True)
    partner_id = fields.Many2one('res.partner', string='Customer')
    csm_user_id = fields.Many2one('res.users', string='CSM Engineer', index=True)
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company)
    currency_id = fields.Many2one(related='company_id.currency_id')
    lifecycle_stage_id = fields.Many2one('cs.stage', string='Lifecycle Stage')
    tier = fields.Selection(related='cs_account_id.tier', store=True)

    # Measures
    health_score = fields.Integer(aggregator='avg')
    mrr = fields.Monetary(currency_field='currency_id')
    csat = fields.Float(string='CSAT (1-5)', aggregator='avg')
    nps = fields.Float(string='Survey Score (%)', aggregator='avg')
    open_tickets = fields.Integer()
    sentiment = fields.Integer(string='Sentiment (-100..100)', aggregator='avg')
    avg_resolution_hours = fields.Float(aggregator='avg')
    meetings = fields.Integer()
    calls_made = fields.Integer(string='Calls Made',
                                help='VoIP calls placed to this account during the period.')
    activities_done = fields.Integer(string='Activities Done',
                                     help='Activities completed on this account during the period '
                                          '(pending AI/system to-dos are not counted).')
    activities = fields.Integer(string='Activities', compute='_compute_activities', store=True,
                                help='Engineer activity in the period: calls placed + activities '
                                     'completed. Use this instead of Count (which counts accounts).')
    upsell_revenue = fields.Monetary(currency_field='currency_id')
    overdue_amount = fields.Monetary(currency_field='currency_id')
    days_since_touch = fields.Integer(aggregator='avg')
    churned = fields.Boolean()
    is_active_customer = fields.Boolean(string='Active Customer')

    @api.depends('calls_made', 'activities_done')
    def _compute_activities(self):
        for rec in self:
            rec.activities = (rec.calls_made or 0) + (rec.activities_done or 0)

    @api.model
    def _period_start(self, period_type, today):
        if period_type == 'week':
            return today - relativedelta(days=today.weekday())
        if period_type == 'quarter':
            month = today.month - (today.month - 1) % 3
            return today.replace(month=month, day=1)
        return today.replace(day=1)

    @api.model
    def _period_end(self, period_type, period_start):
        if period_type == 'week':
            return period_start + relativedelta(days=7)
        if period_type == 'quarter':
            return period_start + relativedelta(months=3)
        return period_start + relativedelta(months=1)

    @api.model
    def _cron_build_snapshot(self, period_type='month'):
        today = fields.Date.context_today(self)
        period_start = self._period_start(period_type, today)
        period_end = self._period_end(period_type, period_start)
        start_dt = fields.Datetime.to_datetime(period_start)
        end_dt = fields.Datetime.to_datetime(period_end)
        Call = self.env['voip.call']
        Message = self.env['mail.message']
        accounts = self.env['cs.account'].search([])
        accounts._recompute_account_metrics()
        for acc in accounts:
            # Actor-based: count what THIS account's engineer (csm) actually did
            # on the account during the period — calls she placed + activities she
            # completed. (Account-based counting would miscredit the owner for
            # calls/work other engineers did on the account.)
            csm = acc.csm_user_id
            if csm and acc.partner_id:
                # Use create_date for the period bound: start_date is often NULL.
                calls_made = Call.search_count([
                    ('user_id', '=', csm.id),
                    ('partner_id.commercial_partner_id', '=', acc.partner_id.id),
                    ('create_date', '>=', start_dt),
                    ('create_date', '<', end_dt),
                ])
                activities_done = Message.search_count([
                    ('model', '=', 'cs.account'),
                    ('res_id', '=', acc.id),
                    ('mail_activity_type_id', '!=', False),
                    ('author_id', '=', csm.partner_id.id),
                    ('date', '>=', start_dt),
                    ('date', '<', end_dt),
                ])
            else:
                calls_made = activities_done = 0
            vals = {
                'period_start': period_start,
                'period_type': period_type,
                'cs_account_id': acc.id,
                'partner_id': acc.partner_id.id,
                'csm_user_id': acc.csm_user_id.id,
                'lifecycle_stage_id': acc.lifecycle_stage_id.id,
                'health_score': acc.health_score,
                'mrr': acc.mrr,
                'csat': acc.csat_latest,
                'nps': acc.nps_latest,
                'open_tickets': acc.open_tickets_count,
                'sentiment': acc.sentiment_score,
                'avg_resolution_hours': acc.avg_resolution_hours,
                'meetings': acc.meeting_count,
                'calls_made': calls_made,
                'activities_done': activities_done,
                'upsell_revenue': acc.upsell_revenue,
                'overdue_amount': acc.overdue_amount,
                'days_since_touch': acc.days_since_touch,
                'churned': acc.subscription_churned,
                'is_active_customer': bool(acc.mrr) and not acc.subscription_churned,
            }
            existing = self.search([
                ('cs_account_id', '=', acc.id),
                ('period_start', '=', period_start),
                ('period_type', '=', period_type)], limit=1)
            if existing:
                existing.write(vals)
            else:
                self.create(vals)
        # Averaged score cells must ignore "no data" accounts so SQL AVG excludes
        # them (an unrated / unanalysed account must not pull the team average
        # toward 0). The ORM cannot store NULL on numeric fields (False -> 0), so
        # normalise the no-data zeros to NULL in SQL after the rows are written.
        # Only AVERAGED score fields are touched — counts/money keep genuine 0s.
        self.env.cr.execute("""
            UPDATE csm_kpi_snapshot
               SET csat = NULLIF(csat, 0),
                   nps = NULLIF(nps, 0),
                   sentiment = NULLIF(sentiment, 0),
                   avg_resolution_hours = NULLIF(avg_resolution_hours, 0)
             WHERE period_start = %s AND period_type = %s
        """, (period_start, period_type))

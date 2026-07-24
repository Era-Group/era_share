# -*- coding: utf-8 -*-
import json
import logging
import re
import threading
from datetime import timedelta

from psycopg2 import errors as psycopg2_errors
from markupsafe import Markup

from odoo import api, fields, models, SUPERUSER_ID, _
from odoo.exceptions import UserError
from odoo.tools import html_escape

from .ai_utils import extract_json_object as _cs_extract_json

_logger = logging.getLogger(__name__)


def _cs_md_to_html(text):
    """Convert the light markdown the AI returns into safe chatter HTML.

    Handles **bold**, bullet dashes and newlines, normalises any stray <br>/</br>
    the model emitted, and escapes everything else so the chatter renders cleanly
    instead of showing literal ``**`` / ``</br>`` tokens. Returns a Markup string
    so message_post treats it as HTML.
    """
    t = text or ''
    # collapse any line-break tags the model produced into real newlines
    t = re.sub(r'</?br\s*/?>', '\n', t, flags=re.IGNORECASE)
    # escape the rest (prevents injection and literal stray tags)
    t = str(html_escape(t))
    # **bold** -> <b>bold</b>
    t = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', t)
    # newlines -> <br/>
    t = t.replace('\n', '<br/>')
    return Markup(t)

ACTIVE_SUB_STATES = ('3_progress', '4_paused')
CHURN_SUB_STATE = '6_churn'

# Touch-point sync only posts events newer than this to the timeline; older history
# is marked synced silently (avoids dumping a customer's whole history into chatter).
_CS_SYNC_RECENCY_DAYS = 2

# Postgres session advisory-lock key so two detached weekly-digest runs never overlap.
_CS_WEEKLY_DIGEST_LOCK = 871423901

# Account sentiment aggregates analysed tickets within this window, with an
# exponential decay (half-life): a recent ticket dominates, an older one still
# contributes a little. Customers open tickets infrequently, so the window is a
# full year — otherwise quiet accounts would always read neutral/0.
_CS_SENTIMENT_WINDOW_DAYS = 365
_CS_SENTIMENT_HALFLIFE_DAYS = 90
_CS_HEALTH_TRACKING_THRESHOLD = 20


class CsAccount(models.Model):
    _name = 'cs.account'
    _description = 'Customer Success Account'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'health_score asc, next_touch_date asc, id desc'
    _rec_name = 'partner_id'
    _check_company_auto = True

    def _mail_track(self, tracked_fields, initial_values):
        """Keep routine health recalculations out of the customer timeline."""
        if 'health_score' in initial_values:
            old_score = initial_values.get('health_score') or 0
            new_score = self.health_score or 0
            if abs(new_score - old_score) < _CS_HEALTH_TRACKING_THRESHOLD:
                tracked_fields = dict(tracked_fields)
                for field_name in ('health_score', 'health_status', 'churn_risk'):
                    tracked_fields.pop(field_name, None)
        return super()._mail_track(tracked_fields, initial_values)

    # ------------------------------------------------------------------
    # Core / assignment
    # ------------------------------------------------------------------
    partner_id = fields.Many2one(
        'res.partner', string='Customer', required=True, tracking=True,
        domain="[('is_company', '=', True)]",
        help="The customer company managed by Customer Success. "
             "One account per company; child contacts are aggregated automatically.")
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        'res.company', string='Company', required=True, index=True,
        default=lambda self: self.env.company)
    currency_id = fields.Many2one(related='company_id.currency_id')
    # Contact channels (phone uses the VoIP click-to-dial widget)
    phone = fields.Char(related='partner_id.phone', string='Phone', readonly=False)
    email = fields.Char(related='partner_id.email', string='Email', readonly=False)

    # Google Sheet / ERA operational fields. These are updated only when their
    # corresponding red title is approved in the synchronization settings.
    sheet_era_csm = fields.Char(string='Sheet: ERA CSM')
    sheet_era_csm_phone = fields.Char(string='Sheet: ERA CSM phone')
    sheet_era_csm_email = fields.Char(string='Sheet: ERA CSM email')
    sheet_customer_name = fields.Char(string='Sheet: Customer Name')
    sheet_date_of_join = fields.Date(string='Sheet: Date of Join')
    sheet_next_invoice_date = fields.Date(string='Sheet: Next invoice date')
    sheet_recurring_plan = fields.Char(string='Sheet: Recurring plan')
    sheet_industry = fields.Char(string='Sheet: Industry')
    sheet_number_of_employees = fields.Integer(string='Sheet: Employees')
    sheet_number_of_users = fields.Integer(string='Sheet: Users')
    sheet_active_users = fields.Integer(string='Sheet: Active Users')
    sheet_stage = fields.Char(string='Sheet: Stage')
    sheet_version = fields.Char(string='Sheet: Version')
    sheet_adoption = fields.Char(string='Sheet: Adoption')
    sheet_client_website = fields.Char(string='Sheet: Client Website')
    sheet_active_implemented_modules = fields.Text(string='Sheet: Active Implemented modules')
    sheet_potential_expansion = fields.Text(string='Sheet: Potential Expansion')
    sheet_next_action = fields.Text(string='Sheet: Next Action')
    sheet_extra_notes = fields.Text(string='Sheet: Extra Notes')
    sheet_expansion_status = fields.Char(string='Sheet: Expansion Status')
    sheet_last_synced_on = fields.Datetime(string='Sheet: Last synced', readonly=True, copy=False)

    def action_fill_google_sheet_fields(self):
        self.ensure_one()
        return self.env['cs.google.sheet.sync'].action_sync_account(self, use_ai=True)

    def action_match_fetch_google_sheet_fields(self):
        self.ensure_one()
        return self.env['cs.google.sheet.sync'].action_sync_account(
            self, use_ai=False, all_fields=True)

    def action_send_google_sheet_fields(self):
        self.ensure_one()
        return self.env['cs.google.sheet.sync'].action_send_account(self)

    csm_user_id = fields.Many2one(
        'res.users', string='Customer Success Engineer', tracking=True,
        help="The CSM engineer responsible for following up with this customer.")
    team_id = fields.Many2one('crm.team', string='Success Team')
    tier = fields.Selection([
        ('strategic', '⭐⭐⭐ Strategic'),
        ('growth', '⭐⭐ Growth'),
        ('core', '⭐ Core'),
        ('long_tail', '▫️ Long Tail'),
    ], string='Segment / Tier', default='core', tracking=True)
    cadence = fields.Selection([
        ('weekly', 'Weekly'),
        ('biweekly', 'Bi-weekly'),
        ('monthly', 'Monthly'),
        ('quarterly', 'Quarterly'),
    ], string='Follow-up Cadence', default='monthly',
        help="Expected follow-up rhythm. Drives the cadence reminder and the "
             "follow-up-level evaluation.")
    success_profile_ids = fields.One2many(
        'cs.success.profile', 'cs_account_id', string='Success Plans')
    success_profile_count = fields.Integer(
        string='Success Plan', compute='_compute_success_plan_metrics')
    open_success_milestone_count = fields.Integer(
        string='Open Success Milestones', compute='_compute_success_plan_metrics')
    next_success_milestone_date = fields.Date(
        string='Next Success Milestone', compute='_compute_success_plan_metrics')
    ai_success_plan_enabled = fields.Boolean(
        related='company_id.cs_ai_success_plan_enabled', readonly=True)
    value_review_ids = fields.One2many(
        'cs.value.review', 'cs_account_id', string='Value Reviews')
    open_value_review_count = fields.Integer(
        string='Open Value Reviews', compute='_compute_value_review_metrics')
    next_value_review_date = fields.Date(
        string='Next Value Review', compute='_compute_value_review_metrics')
    adoption_assessment_ids = fields.One2many(
        'cs.adoption.assessment', 'cs_account_id', string='Adoption Assessments')
    adoption_assessment_count = fields.Integer(
        compute='_compute_adoption_metrics', string='# Adoption Assessments')
    latest_adoption_score = fields.Float(
        compute='_compute_adoption_metrics', string='Adoption Score')
    latest_adoption_confidence = fields.Float(
        compute='_compute_adoption_metrics', string='Adoption Data Confidence')
    latest_adoption_status = fields.Selection([
        ('unknown', 'Unknown'), ('healthy', 'Healthy'),
        ('watch', 'Needs Attention'), ('low', 'Low Adoption'),
    ], compute='_compute_adoption_metrics', string='Adoption Status')
    latest_adoption_date = fields.Date(
        compute='_compute_adoption_metrics', string='Latest Adoption Assessment')
    next_adoption_assessment_date = fields.Date(
        compute='_compute_adoption_metrics', string='Next Adoption Review')
    voc_insight_ids = fields.One2many(
        'cs.voc.insight', 'cs_account_id', string='Voice of Customer')
    open_voc_count = fields.Integer(
        string='Open Customer Insights', compute='_compute_voc_metrics')
    high_voc_count = fields.Integer(
        string='High-Priority Customer Insights', compute='_compute_voc_metrics')

    lifecycle_stage_id = fields.Many2one(
        'cs.stage', string='Lifecycle Stage', tracking=True, index=True,
        group_expand='_read_group_stage_ids',
        default=lambda self: self.env['cs.stage'].search([], order='sequence', limit=1))
    onboarding_start_date = fields.Date(
        string='Onboarding Start', default=fields.Date.context_today)

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------
    health_score = fields.Integer(string='Health Score', tracking=True, readonly=True, aggregator='avg')
    health_status = fields.Selection([
        ('good', 'Good'),
        ('watch', 'Watch'),
        ('at_risk', 'At Risk'),
        ('critical', 'Critical'),
    ], string='Health', default='watch', tracking=True, readonly=True)
    churn_risk = fields.Boolean(string='Churn Risk', tracking=True, readonly=True)
    kanban_color = fields.Integer(string='Kanban Color', readonly=True)
    # AI churn forecast (predictive, grounded on snapshot history + situation)
    churn_probability = fields.Integer(string='Churn Probability 90d (%)', readonly=True, aggregator='avg')
    churn_prob_30 = fields.Integer(string='Churn 30d (%)', readonly=True, aggregator='avg')
    churn_prob_60 = fields.Integer(string='Churn 60d (%)', readonly=True, aggregator='avg')
    churn_confidence = fields.Selection([
        ('low', 'Low'), ('medium', 'Medium'), ('high', 'High'),
    ], string='Forecast Confidence', readonly=True)
    churn_factors = fields.Text(string='Churn Drivers', readonly=True)
    churn_forecast_date = fields.Datetime(string='Churn Forecast On', readonly=True)
    usage_signal = fields.Selection([
        ('active', 'Active'),
        ('low', 'Low'),
        ('inactive', 'Inactive'),
    ], string='System Usage', default='active',
        help="Whether the customer is using the system correctly / actively.")

    # ------------------------------------------------------------------
    # Follow-up
    # ------------------------------------------------------------------
    last_touch_date = fields.Datetime(string='Last Touch', readonly=True)
    next_touch_date = fields.Date(string='Next Planned Touch', readonly=True)
    days_since_touch = fields.Integer(
        string='Days Since Last Touch', compute='_compute_days_since_touch')
    meeting_count = fields.Integer(string='# Meetings', compute='_compute_counts')

    # ------------------------------------------------------------------
    # Subscriptions / revenue (from sale_subscription)
    # ------------------------------------------------------------------
    mrr = fields.Monetary(string='MRR', readonly=True, currency_field='currency_id')
    renewal_date = fields.Date(string='Next Renewal', readonly=True, tracking=True)
    renewal_soon = fields.Boolean(string='Renewal Soon (<90d)', readonly=True)
    days_to_renewal = fields.Integer(
        string='Days to Renewal', compute='_compute_days_to_renewal')
    subscription_count = fields.Integer(string='# Subscriptions', compute='_compute_counts')
    subscription_churned = fields.Boolean(string='Subscription Churned', readonly=True)
    # AI renewal strategy (window-gated)
    renewal_lever = fields.Selection([
        ('pitch_first', 'Pitch / Value first'),
        ('collections_first', 'Collections first'),
        ('re_engage', 'Re-engage / Recover'),
        ('standard', 'Standard renewal'),
    ], string='Renewal Lever', readonly=True)
    renewal_risk = fields.Selection([
        ('likely', 'Likely'), ('at_risk', 'At Risk'), ('critical', 'Critical'),
    ], string='Renewal Outlook', readonly=True)
    renewal_strategy = fields.Text(string='Renewal Strategy', readonly=True)
    renewal_strategy_date = fields.Datetime(string='Strategy Generated On', readonly=True)

    # ------------------------------------------------------------------
    # Support (from helpdesk)
    # ------------------------------------------------------------------
    open_tickets_count = fields.Integer(string='# Open Tickets', compute='_compute_counts')
    ticket_total_count = fields.Integer(string='# Tickets (All)', compute='_compute_counts')
    avg_resolution_hours = fields.Float(string='Avg Resolution (h)', compute='_compute_counts')
    sla_failed_count = fields.Integer(string='# Failed SLA', compute='_compute_counts')
    support_wallet_count = fields.Integer(
        string='Support Packages', compute='_compute_support_wallet_metrics')
    support_hours_purchased = fields.Float(
        string='Support Hours Purchased', compute='_compute_support_wallet_metrics')
    support_hours_used = fields.Float(
        string='Support Hours Used', compute='_compute_support_wallet_metrics')
    support_hours_remaining = fields.Float(
        string='Support Hours Remaining', compute='_compute_support_wallet_metrics')
    support_wallet_status = fields.Selection([
        ('none', 'No Package'),
        ('healthy', 'Healthy'),
        ('expiring', 'Expiring Soon'),
        ('low', 'Low Balance'),
        ('critical', 'Critical Balance'),
        ('exhausted', 'Exhausted'),
        ('expired', 'Expired'),
    ], string='Support Hours Status', compute='_compute_support_wallet_metrics')

    # ------------------------------------------------------------------
    # Satisfaction (rating / survey)
    # ------------------------------------------------------------------
    csat_latest = fields.Float(string='Latest CSAT (1-5)', readonly=True, aggregator='avg')
    nps_latest = fields.Float(string='Latest Survey Score (%)', readonly=True, aggregator='avg')
    survey_count = fields.Integer(string='# Surveys', compute='_compute_counts')

    # AI sentiment (decayed aggregate of analysed tickets over the sentiment window)
    sentiment_score = fields.Integer(string='Sentiment Score (-100..100)', readonly=True, aggregator='avg')
    sentiment_label = fields.Selection([
        ('positive', 'Positive'),
        ('neutral', 'Neutral'),
        ('negative', 'Negative'),
    ], string='Sentiment', readonly=True)
    sentiment_detail = fields.Text(
        string='Sentiment Analysis Detail', compute='_compute_sentiment_detail',
        help="Per-ticket breakdown behind the aggregate sentiment, shown on hover.")

    @api.depends('sentiment_label', 'sentiment_score', 'partner_id')
    def _compute_sentiment_detail(self):
        """Human-readable breakdown of the tickets behind the aggregate sentiment
        (the contributing analysed tickets within the sentiment window, newest first).
        Surfaced as the hover tooltip on the sentiment badge."""
        Ticket = self.env['helpdesk.ticket']
        now = fields.Datetime.now()
        cutoff = now - timedelta(days=_CS_SENTIMENT_WINDOW_DAYS)
        mood = {'positive': '🙂', 'neutral': '😐', 'negative': '🙁'}
        for acc in self:
            pids = acc._partner_ids() if acc.partner_id else []
            tickets = Ticket.search([
                ('partner_id', 'in', pids), ('cs_sentiment_analyzed', '=', True),
                ('cs_sentiment_label', '!=', False), ('create_date', '>=', cutoff),
            ], order='create_date desc') if pids else Ticket.browse()
            if not tickets:
                acc.sentiment_detail = _(
                    "No analysed support tickets in the last 12 months — "
                    "sentiment is neutral by default.")
                continue
            label_map = {'positive': _('Positive'), 'neutral': _('Neutral'),
                         'negative': _('Negative')}
            lines = [_(
                "Aggregate: %(label)s (%(score)s). Based on %(n)s analysed ticket(s) "
                "in the last 12 months — recent tickets weighted higher.",
                label=label_map.get(acc.sentiment_label, acc.sentiment_label or '-'),
                score=acc.sentiment_score, n=len(tickets))]
            for t in tickets[:8]:
                day = t.create_date.date() if t.create_date else ''
                reason = (t.cs_sentiment_reason or '').strip()
                lines.append("%s %s · %s (%s) — %s%s" % (
                    mood.get(t.cs_sentiment_label, ''), day,
                    t.cs_sentiment_label, t.cs_sentiment_score,
                    (t.name or '')[:50], "\n   ↳ %s" % reason if reason else ''))
            if len(tickets) > 8:
                lines.append(_("… and %s more.", len(tickets) - 8))
            acc.sentiment_detail = "\n".join(lines)

    # ------------------------------------------------------------------
    # Collections (account_followup)
    # ------------------------------------------------------------------
    overdue_amount = fields.Monetary(
        string='Overdue', readonly=True, currency_field='currency_id')
    followup_status = fields.Selection(related='partner_id.followup_status', string='Follow-up Status')

    # ------------------------------------------------------------------
    # Growth (crm)
    # ------------------------------------------------------------------
    opportunity_count = fields.Integer(string='# Opportunities', compute='_compute_counts')
    upsell_revenue = fields.Monetary(
        string='Upsell Revenue (Won)', readonly=True, currency_field='currency_id')
    project_count = fields.Integer(string='# Projects', compute='_compute_counts')
    call_count = fields.Integer(string='# Calls', compute='_compute_counts')
    whatsapp_count = fields.Integer(string='# WhatsApp', compute='_compute_counts')
    offering_ids = fields.One2many('csm.offering', 'cs_account_id', string='Offerings')
    offering_count = fields.Integer(string='# Offerings', compute='_compute_offering_count')

    # AI Next Best Action
    next_action = fields.Text(string='Next Best Action', readonly=True, copy=False)
    next_action_reason = fields.Text(string='Action Reason', readonly=True, copy=False)
    next_action_sources = fields.Text(string='Action Evidence Sources', readonly=True, copy=False)
    next_action_priority = fields.Selection([
        ('low', 'Low'),
        ('medium', 'Medium'),
        ('high', 'High'),
        ('urgent', 'Urgent'),
    ], string='Action Priority', readonly=True, copy=False)
    next_action_date = fields.Date(string='Action By', readonly=True, copy=False)
    next_action_generated_on = fields.Datetime(string='Suggested On', readonly=True, copy=False)
    next_action_log_ids = fields.One2many(
        'cs.next.action', 'cs_account_id', string='AI Suggestions History')

    # AI profile summary (shown under the customer name)
    ai_summary = fields.Text(string='AI Profile Summary', readonly=True, copy=False)
    ai_summary_date = fields.Datetime(string='Summary Generated On', readonly=True, copy=False)

    _partner_uniq = models.Constraint(
        'unique(partner_id)',
        'A Customer Success account already exists for this customer.')

    # ==================================================================
    # Compute (cheap / live)
    # ==================================================================
    @api.depends('offering_ids')
    def _compute_offering_count(self):
        for acc in self:
            acc.offering_count = len(acc.offering_ids)

    @api.depends(
        'success_profile_ids', 'success_profile_ids.milestone_ids.state',
        'success_profile_ids.milestone_ids.target_date')
    def _compute_success_plan_metrics(self):
        for account in self:
            milestones = account.success_profile_ids.milestone_ids.filtered(
                lambda milestone: milestone.state not in ('achieved', 'cancelled'))
            account.success_profile_count = len(account.success_profile_ids)
            account.open_success_milestone_count = len(milestones)
            dates = milestones.mapped('target_date')
            account.next_success_milestone_date = min(dates) if dates else False

    @api.depends('partner_id', 'company_id')
    def _compute_support_wallet_metrics(self):
        Wallet = self.env['cs.support.wallet'].sudo()
        status_rank = {
            'none': -1, 'healthy': 0, 'expiring': 1, 'low': 2,
            'critical': 3, 'expired': 4, 'exhausted': 5,
        }
        wallets_by_account = {account.id: Wallet.browse() for account in self}
        for wallet in Wallet.search([('cs_account_id', 'in', self.ids)]):
            wallets_by_account[wallet.cs_account_id.id] |= wallet
        today = fields.Date.context_today(self)
        for account in self:
            wallets = wallets_by_account[account.id]
            active_wallets = wallets.filtered(
                lambda wallet: wallet.expiry_date >= today and wallet.remaining_hours > 0)
            attention_wallets = (
                active_wallets.filtered('attention_rank')
                if active_wallets else wallets.filtered('attention_rank'))
            status_wallets = attention_wallets or active_wallets or wallets
            account.support_wallet_count = len(wallets)
            account.support_hours_purchased = sum(active_wallets.mapped('purchased_hours'))
            account.support_hours_used = sum(active_wallets.mapped('used_hours'))
            account.support_hours_remaining = sum(active_wallets.mapped('remaining_hours'))
            account.support_wallet_status = max(
                status_wallets.mapped('status'), key=lambda status: status_rank.get(status, 0),
                default='none')

    @api.depends('value_review_ids.state', 'value_review_ids.review_date')
    def _compute_value_review_metrics(self):
        for account in self:
            reviews = account.value_review_ids.filtered(
                lambda review: review.state not in ('closed', 'cancelled'))
            account.open_value_review_count = len(reviews)
            dates = reviews.mapped('review_date')
            account.next_value_review_date = min(dates) if dates else False

    @api.depends(
        'adoption_assessment_ids.state', 'adoption_assessment_ids.assessment_date',
        'adoption_assessment_ids.next_assessment_date',
        'adoption_assessment_ids.score', 'adoption_assessment_ids.confidence',
        'adoption_assessment_ids.status')
    def _compute_adoption_metrics(self):
        for account in self:
            confirmed = account.adoption_assessment_ids.filtered(
                lambda item: item.state == 'confirmed').sorted(
                    lambda item: (item.assessment_date, item.id), reverse=True)
            latest = confirmed[:1]
            account.adoption_assessment_count = len(account.adoption_assessment_ids)
            account.latest_adoption_score = latest.score if latest else 0.0
            account.latest_adoption_confidence = latest.confidence if latest else 0.0
            account.latest_adoption_status = latest.status if latest else 'unknown'
            account.latest_adoption_date = latest.assessment_date if latest else False
            account.next_adoption_assessment_date = latest.next_assessment_date if latest else False

    @api.depends('voc_insight_ids.state', 'voc_insight_ids.priority')
    def _compute_voc_metrics(self):
        for account in self:
            open_insights = account.voc_insight_ids.filtered(
                lambda insight: insight.state in ('new', 'triaged', 'acted'))
            account.open_voc_count = len(open_insights)
            account.high_voc_count = len(open_insights.filtered(
                lambda insight: insight.priority == 'high'))

    @api.depends('partner_id')
    def _compute_counts(self):
        """Live counts for the smart buttons – always accurate on form/kanban load
        (not dependent on the nightly recompute cron)."""
        Ticket = self.env['helpdesk.ticket'].sudo()
        Sale = self.env['sale.order'].sudo()
        Lead = self.env['crm.lead'].sudo()
        Event = self.env['calendar.event'].sudo()
        SurveyInput = self.env['survey.user_input'].sudo()
        Call = self.env['voip.call'].sudo() if 'voip.call' in self.env.registry else None
        WaChannel = self.env['discuss.channel'].sudo() \
            if 'whatsapp.message' in self.env.registry else None
        Project = self.env['project.project'].sudo() if 'project.project' in self.env.registry else None
        for acc in self:
            pids = acc._partner_ids()
            if not pids:
                acc.open_tickets_count = acc.ticket_total_count = acc.sla_failed_count = 0
                acc.avg_resolution_hours = 0.0
                acc.subscription_count = acc.opportunity_count = acc.call_count = 0
                acc.whatsapp_count = acc.meeting_count = acc.survey_count = acc.project_count = 0
                continue
            acc.open_tickets_count = Ticket.search_count(
                [('partner_id', 'in', pids), ('stage_id.fold', '=', False)])
            acc.ticket_total_count = Ticket.search_count([('partner_id', 'in', pids)])
            acc.sla_failed_count = Ticket.search_count(
                [('partner_id', 'in', pids), ('stage_id.fold', '=', False), ('sla_fail', '=', True)])
            closed = Ticket.search([('partner_id', 'in', pids), ('close_date', '!=', False)])
            acc.avg_resolution_hours = round(
                sum(closed.mapped('close_hours')) / len(closed), 1) if closed else 0.0
            acc.subscription_count = Sale.search_count(
                [('partner_id', 'in', pids), ('is_subscription', '=', True)])
            acc.opportunity_count = Lead.search_count(
                [('partner_id', 'in', pids), ('type', '=', 'opportunity')])
            acc.meeting_count = Event.search_count([('partner_ids', 'in', pids)])
            acc.survey_count = SurveyInput.search_count(
                [('partner_id', 'in', pids), ('state', '=', 'done')])
            acc.call_count = Call.search_count([('partner_id', 'in', pids)]) if Call is not None else 0
            acc.whatsapp_count = WaChannel.search_count(
                [('channel_type', '=', 'whatsapp'), ('channel_partner_ids', 'in', pids)]) \
                if WaChannel is not None else 0
            acc.project_count = Project.search_count([('partner_id', 'in', pids)]) if Project is not None else 0

    @api.depends('last_touch_date')
    def _compute_days_since_touch(self):
        today = fields.Date.context_today(self)
        for acc in self:
            if acc.last_touch_date:
                acc.days_since_touch = (today - acc.last_touch_date.date()).days
            else:
                acc.days_since_touch = 0

    @api.depends('renewal_date')
    def _compute_days_to_renewal(self):
        today = fields.Date.context_today(self)
        for acc in self:
            acc.days_to_renewal = (acc.renewal_date - today).days if acc.renewal_date else 0

    @api.model
    def _read_group_stage_ids(self, stages, domain):
        return self.env['cs.stage'].search([], order='sequence')

    # ==================================================================
    # Partner scope helpers
    # ==================================================================
    def _partner_ids(self):
        """All partner ids belonging to this customer (company + descendants)."""
        self.ensure_one()
        if not self.partner_id:
            return []
        commercial = self.partner_id.commercial_partner_id or self.partner_id
        return self.env['res.partner'].search([('id', 'child_of', commercial.id)]).ids

    # ==================================================================
    # Create / write – linking, mirroring, lifecycle
    # ==================================================================
    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.user.has_group('era_customer_success.group_era_cs_manager'):
            for vals in vals_list:
                if vals.get('csm_user_id') not in (False, self.env.user.id):
                    raise UserError(_('Only a Customer Success Manager can assign accounts to another user.'))
        accounts = super().create(vals_list)
        accounts._sync_partner_link()
        accounts._mirror_csm_to_partner()
        accounts._recompute_account_metrics()
        accounts._launch_kickoff()
        return accounts

    def write(self, vals):
        if ('csm_user_id' in vals
                and not self.env.user.has_group('era_customer_success.group_era_cs_manager')):
            raise UserError(_('Only a Customer Success Manager can change the assigned CSM.'))
        old_partner_ids = set()
        if 'partner_id' in vals:
            for account in self:
                old_partner_ids.update(account._partner_ids())
        res = super().write(vals)
        if 'partner_id' in vals:
            new_partner_ids = set()
            for account in self:
                new_partner_ids.update(account._partner_ids())
            stale_partners = self.env['res.partner'].browse(old_partner_ids - new_partner_ids).filtered(
                lambda partner: partner.cs_account_id in self)
            stale_partners.with_context(skip_cs_mirror=True).write({'cs_account_id': False})
            self._sync_partner_link()
        if 'csm_user_id' in vals and not self.env.context.get('skip_cs_mirror'):
            self._mirror_csm_to_partner()
        if 'lifecycle_stage_id' in vals:
            self._on_stage_change()
        # Auto-refresh the stored metrics after any change to the record. This is a
        # pure DB recompute (no AI, no external cost), so it is safe to run on every
        # edit. The cs_skip_recompute guard stops the recompute's own field writes
        # from re-triggering it (and breaks any write recursion).
        if not self.env.context.get('cs_skip_recompute'):
            self.with_context(cs_skip_recompute=True)._recompute_account_metrics()
        return res

    def _sync_partner_link(self):
        for acc in self:
            if not acc.partner_id:
                continue
            partners = self.env['res.partner'].search(
                ['|', ('id', '=', acc.partner_id.id),
                 ('commercial_partner_id', '=', acc.partner_id.id)])
            partners.with_context(skip_cs_mirror=True).write({'cs_account_id': acc.id})

    def _mirror_csm_to_partner(self):
        for acc in self:
            if not (acc.partner_id and acc.csm_user_id):
                continue
            vals = {'user_id': acc.csm_user_id.id}
            if 'followup_responsible_id' in acc.partner_id._fields:
                vals['followup_responsible_id'] = acc.csm_user_id.id
            acc.partner_id.with_context(skip_cs_mirror=True).sudo().write(vals)

    def _on_stage_change(self):
        for acc in self:
            stage = acc.lifecycle_stage_id
            if stage.mail_template_id and acc.partner_id.email:
                stage.mail_template_id.send_mail(acc.id, force_send=False)

    def _auto_activity_schedule(self, **kwargs):
        """Schedule a system/AI-generated activity as OdooBot.

        Module-automated activities (kickoff, cadence, renewal, at-risk,
        survey/service recovery, renewal play…) are created with
        ``created_by = OdooBot`` so they are clearly distinguishable from
        activities an employee schedules personally. This keeps activity /
        workload reports realistic: real employee activity is everything NOT
        created by OdooBot. (Employee-initiated actions such as Log Call keep
        the employee as creator.)"""
        return self.with_user(SUPERUSER_ID).activity_schedule(**kwargs)

    def _launch_kickoff(self):
        todo = self.env.ref('mail.mail_activity_data_todo', raise_if_not_found=False)
        for acc in self:
            if not acc.csm_user_id:
                continue
            acc._auto_activity_schedule(
                act_type_xmlid='mail.mail_activity_data_todo' if todo else False,
                date_deadline=fields.Date.context_today(self) + timedelta(days=1),
                summary=_('Customer Success kickoff – welcome the customer'),
                user_id=acc.csm_user_id.id)

    # ==================================================================
    # Metrics recompute (writes plain stored fields – called by cron/button)
    # ==================================================================
    def action_recompute_health(self):
        self._recompute_account_metrics()
        return True

    # ==================================================================
    # AI – Next Best Action
    # ==================================================================
    @api.model
    def _ai_enabled_company_ids(self, setting):
        return self.env['res.company'].search([(setting, '=', True)]).ids

    def _build_situation_summary(self):
        """Textual snapshot of the customer (situation + history + last suggestion)
        fed to the Next-Best-Action AI agent."""
        self.ensure_one()
        # Read with sudo: this read-only summary aggregates account data (incl.
        # accounting-gated fields like followup_status) into an AI prompt, and is
        # called from user-facing wizards (follow-up compose, call briefing,
        # copilot) whose CS users may lack rights to every field. The caller has
        # already passed access control to reach the account.
        self = self.sudo()
        today = fields.Date.context_today(self)
        tenure = (today - (self.onboarding_start_date or today)).days
        L = [
            "Customer: %s (tier: %s, lifecycle stage: %s)" % (
                self.partner_id.name, self.tier or '-',
                self.lifecycle_stage_id.name or '-'),
            "Tenure with us: %s days. CSM engineer: %s." % (
                tenure, self.csm_user_id.name or '-'),
            "Health: %s/100 (%s). Churn risk: %s." % (
                self.health_score, self.health_status, 'yes' if self.churn_risk else 'no'),
            "MRR: %s. Next renewal: %s (in %s days). Subscriptions: %s. Churned subscription: %s." % (
                self.mrr, self.renewal_date or '-', self.days_to_renewal,
                self.subscription_count, 'yes' if self.subscription_churned else 'no'),
            "Open tickets: %s (SLA failed: %s). Avg resolution: %s h. Total tickets: %s." % (
                self.open_tickets_count, self.sla_failed_count,
                self.avg_resolution_hours, self.ticket_total_count),
            "CSAT: %s/5. Survey score: %s%%. Sentiment: %s (%s)." % (
                self.csat_latest, self.nps_latest, self.sentiment_label or '-',
                self.sentiment_score),
            "Overdue amount: %s (collections status: %s)." % (
                self.overdue_amount, self.followup_status or '-'),
            "Usage signal: %s. Last touch: %s (%s days ago). Next planned touch: %s." % (
                self.usage_signal or '-', self.last_touch_date or '-',
                self.days_since_touch, self.next_touch_date or '-'),
            "Interactions: calls %s, whatsapp %s, meetings %s, opportunities %s, offerings %s." % (
                self.call_count, self.whatsapp_count, self.meeting_count,
                self.opportunity_count, self.offering_count),
        ]
        latest_adoption = self.env['cs.adoption.assessment'].sudo().search([
            ('cs_account_id', '=', self.id), ('state', '=', 'confirmed'),
        ], order='assessment_date desc, id desc', limit=1)
        if latest_adoption:
            L.append(
                "Latest adoption assessment (%s): score %s%%, data confidence %s%%, "
                "status %s. Blocker: %s. Enablement plan: %s." % (
                    latest_adoption.assessment_date,
                    latest_adoption.score,
                    latest_adoption.confidence,
                    latest_adoption.status,
                    (latest_adoption.blockers or '-')[:500],
                    (latest_adoption.enablement_plan or '-')[:500],
                )
            )
        else:
            L.append("No confirmed adoption assessment is available.")

        open_voc = self.env['cs.voc.insight'].sudo().search([
            ('cs_account_id', '=', self.id),
            ('state', 'in', ('new', 'triaged', 'acted')),
        ], order='priority_rank desc, insight_date desc, id desc', limit=3)
        if open_voc:
            L.append("Open Voice of Customer insights:\n- " + "\n- ".join(
                "%s | %s sentiment | %s priority | %s" % (
                    insight.theme, insight.sentiment, insight.priority,
                    (insight.summary or insight.suggestion or '-')[:500],
                ) for insight in open_voc
            ))
        else:
            L.append("No open Voice of Customer insights.")

        latest_review = self.env['cs.value.review'].sudo().search([
            ('cs_account_id', '=', self.id), ('state', '=', 'closed'),
        ], order='review_date desc, id desc', limit=1)
        if latest_review:
            L.append(
                "Latest closed value review (%s, period %s to %s): confirmed value: %s. "
                "Risks: %s. Commitments: %s. Next step: %s on %s." % (
                    latest_review.review_date,
                    latest_review.period_start,
                    latest_review.period_end,
                    (latest_review.value_realized or '-')[:500],
                    (latest_review.risks_and_blockers or '-')[:500],
                    (latest_review.commitments or '-')[:500],
                    latest_review.next_step or '-',
                    latest_review.next_step_date or '-',
                )
            )
        else:
            L.append("No closed value review is available.")
        msgs = self.message_ids.filtered(lambda m: m.body)[:8]
        hist = [re.sub(r'<[^>]+>', ' ', (m.body or '')).strip()[:160] for m in msgs]
        hist = [h for h in hist if h]
        if hist:
            L.append("Recent timeline:\n- " + "\n- ".join(hist))
        if self.next_action:
            L.append("LAST suggested action (on %s): %s" % (
                self.next_action_generated_on or '-', self.next_action))
        else:
            L.append("No previous suggestion.")
        return "\n".join(L)

    def action_suggest_next_step(self):
        """Ask the AI agent for the next best action for each account."""
        self.check_access('read')
        if any(not account.company_id.cs_ai_next_action_enabled for account in self):
            raise UserError(_('Enable AI Next Best Action in Customer Success settings first.'))
        agent = self.env.ref('era_customer_success.cs_next_action_agent', raise_if_not_found=False)
        if not agent:
            raise UserError(_('The Next Best Action AI agent is not available.'))
        root = self.env.ref('base.user_root')
        for acc in self:
            prompt = acc._build_situation_summary()
            try:
                response = agent.with_user(root).get_direct_response(prompt=prompt)
                raw = response[0] if response else ''
                data = _cs_extract_json(raw)
            except Exception as e:
                _logger.warning("CS next-action generation skipped for account %s (AI error): %s", acc.id, e)
                continue
            if not isinstance(data, dict) or not (data.get('next_action') or '').strip():
                continue
            action = data['next_action'].strip()
            prio = (data.get('priority') or 'medium').lower()
            if prio not in ('low', 'medium', 'high', 'urgent'):
                prio = 'medium'
            try:
                offset = max(0, int(data.get('suggested_in_days') or 0))
            except (TypeError, ValueError):
                offset = 0
            sdate = fields.Date.context_today(acc) + timedelta(days=offset)
            reason = data.get('reason') or ''
            sources = acc._next_action_evidence_sources()
            acc.write({
                'next_action': action,
                'next_action_reason': reason,
                'next_action_sources': sources,
                'next_action_priority': prio,
                'next_action_date': sdate,
                'next_action_generated_on': fields.Datetime.now(),
            })
            self.env['cs.next.action'].create({
                'cs_account_id': acc.id, 'name': action, 'reason': reason,
                'evidence_sources': sources,
                'priority': prio, 'suggested_date': sdate,
                'generated_on': fields.Datetime.now(),
            })
            # NOTE: intentionally NOT posted to the chatter — the next best action
            # is shown in the form field (and kept in the cs.next.action history log).
        return True

    def _next_action_evidence_sources(self):
        """Return visible, factual sources behind an AI next-step suggestion."""
        self.ensure_one()
        sources = []
        if self.open_tickets_count:
            sources.append(_('Open support tickets (%s)', self.open_tickets_count))
        if self.sla_failed_count:
            sources.append(_('Failed support SLA (%s)', self.sla_failed_count))
        if self.renewal_date:
            sources.append(_('Renewal date (%s)', self.renewal_date))
        if self.latest_adoption_date:
            sources.append(_('Latest customer engagement assessment (%s)', self.latest_adoption_date))
        if self.env['cs.voc.insight'].sudo().search_count([
            ('cs_account_id', '=', self.id), ('state', 'in', ('new', 'triaged', 'acted')),
        ]):
            sources.append(_('Open Voice of Customer insight'))
        if self.env['cs.value.review'].sudo().search_count([
            ('cs_account_id', '=', self.id), ('state', '=', 'closed'),
        ]):
            sources.append(_('Latest closed value review'))
        if self.last_touch_date:
            sources.append(_('Last customer contact (%s)', self.last_touch_date))
        return '\n'.join('- %s' % source for source in sources) or _('Current customer account indicators')

    @api.model
    def _cron_suggest_next_steps(self, limit=20):
        """Generate next-best-action suggestions for active accounts (opt-in),
        worst-health first, skipping those suggested in the last 7 days."""
        company_ids = self._ai_enabled_company_ids('cs_ai_next_action_enabled')
        if not company_ids:
            return
        stale = fields.Datetime.now() - timedelta(days=7)
        accounts = self.search([
            ('lifecycle_stage_id.is_churned', '=', False),
            ('company_id', 'in', company_ids),
            ('csm_user_id', '!=', False),
            '|', ('next_action_generated_on', '=', False),
            ('next_action_generated_on', '<', stale),
        ], order='health_score asc', limit=limit)
        if accounts:
            self._run_ai_cron_per_account(accounts, 'action_suggest_next_step')

    def _recompute_account_metrics(self):
        Ticket = self.env['helpdesk.ticket'].sudo()
        Sale = self.env['sale.order'].sudo()
        Rating = self.env['rating.rating'].sudo()
        SurveyInput = self.env['survey.user_input'].sudo()
        Lead = self.env['crm.lead'].sudo()
        Call = self.env['voip.call'].sudo() if 'voip.call' in self.env else None
        Project = self.env['project.project'].sudo() if 'project.project' in self.env else None
        Event = self.env['calendar.event'].sudo()

        for acc in self:
            pids = acc._partner_ids()
            if not pids:
                continue
            vals = {}

            # --- Support (locals for health; the counts themselves are live-computed) ---
            open_tickets = Ticket.search_count(
                [('partner_id', 'in', pids), ('stage_id.fold', '=', False)])
            sla_failed = Ticket.search_count(
                [('partner_id', 'in', pids), ('stage_id.fold', '=', False), ('sla_fail', '=', True)])

            # --- Subscriptions / MRR / renewal ---
            subs = Sale.search([('partner_id', 'in', pids), ('is_subscription', '=', True)])
            active_subs = subs.filtered(lambda s: s.subscription_state in ACTIVE_SUB_STATES)
            vals['mrr'] = sum(active_subs.mapped('recurring_monthly'))
            renewal_dates = active_subs.filtered('next_invoice_date').mapped('next_invoice_date')
            rd = min(renewal_dates) if renewal_dates else False
            vals['renewal_date'] = rd
            today = fields.Date.context_today(self)
            vals['renewal_soon'] = bool(rd and today <= rd <= today + timedelta(days=90))
            vals['subscription_churned'] = bool(
                not active_subs and subs.filtered(lambda s: s.subscription_state == CHURN_SUB_STATE))

            # --- Satisfaction ---
            ticket_ids = Ticket.search([('partner_id', 'in', pids)]).ids
            last_rating = Rating.search(
                [('res_model', '=', 'helpdesk.ticket'), ('res_id', 'in', ticket_ids),
                 ('consumed', '=', True)], order='write_date desc', limit=1) if ticket_ids else Rating
            vals['csat_latest'] = last_rating.rating if last_rating else 0.0
            last_survey = SurveyInput.search(
                [('partner_id', 'in', pids), ('state', '=', 'done')],
                order='create_date desc', limit=1)
            vals['nps_latest'] = last_survey.scoring_percentage if last_survey else 0.0

            # --- AI sentiment (decayed aggregate over analysed tickets) ---
            now = fields.Datetime.now()
            cutoff = now - timedelta(days=_CS_SENTIMENT_WINDOW_DAYS)
            sent_tickets = Ticket.search([
                ('partner_id', 'in', pids), ('cs_sentiment_analyzed', '=', True),
                ('cs_sentiment_label', '!=', False), ('create_date', '>=', cutoff)])
            if sent_tickets:
                wsum = tsum = 0.0
                for t in sent_tickets:
                    days = (now - t.create_date).days
                    weight = 0.5 ** (days / float(_CS_SENTIMENT_HALFLIFE_DAYS))
                    wsum += t.cs_sentiment_score * weight
                    tsum += weight
                savg = wsum / tsum if tsum else 0.0
                vals['sentiment_score'] = round(savg)
                vals['sentiment_label'] = (
                    'positive' if savg > 20 else 'negative' if savg < -20 else 'neutral')
            else:
                vals['sentiment_score'] = 0
                vals['sentiment_label'] = False

            # --- Growth ---
            won_upsell = Lead.search([
                ('partner_id', 'in', pids), ('cs_is_upsell', '=', True),
                ('stage_id.is_won', '=', True)])
            vals['upsell_revenue'] = sum(won_upsell.mapped('expected_revenue'))

            # --- Collections (account_followup) ---
            vals['overdue_amount'] = acc.partner_id.total_overdue or 0.0

            # --- Touches (last_touch_date) ---
            last_dates = []
            if Call is not None:
                last_call = Call.search([('partner_id', 'in', pids)], order='start_date desc', limit=1)
                if last_call.start_date:
                    last_dates.append(last_call.start_date)
            done_msg = acc.message_ids.filtered(lambda m: m.message_type in ('comment', 'email'))[:1]
            if done_msg:
                last_dates.append(done_msg.date)
            vals['last_touch_date'] = max(last_dates) if last_dates else acc.last_touch_date

            open_acts = acc.activity_ids.filtered('date_deadline')
            vals['next_touch_date'] = min(open_acts.mapped('date_deadline')) if open_acts else False

            # --- Health ---
            health_inputs = dict(vals, open_tickets_count=open_tickets, sla_failed_count=sla_failed)
            vals.update(acc._compute_health_values(health_inputs))
            acc.write(vals)

    def _cfg(self, key, default):
        val = self.env['ir.config_parameter'].sudo().get_param(
            'era_customer_success.%s' % key)
        if not val:
            return default
        try:
            return float(val)
        except (TypeError, ValueError):
            return default

    def _compute_health_values(self, vals):
        """Return dict with health_score, health_status, churn_risk, kanban_color."""
        self.ensure_one()

        def clamp(x):
            return max(0.0, min(100.0, x))

        # Support
        support = clamp(100 - 15 * vals.get('open_tickets_count', 0)
                        - 20 * vals.get('sla_failed_count', 0))
        # Collections
        overdue = vals.get('overdue_amount', self.overdue_amount or 0.0) or 0.0
        mrr = vals.get('mrr', 0.0) or 0.0
        if overdue <= 0:
            collections = 100.0
        elif mrr > 0:
            collections = clamp(100 - (overdue / mrr) * 100)
        else:
            collections = 0.0
        # Recency (smooth exponential decay, 90-day half-life)
        last_touch = vals.get('last_touch_date')
        if last_touch:
            days = max(0, (fields.Datetime.now() - last_touch).days)
            recency = clamp(100 * (0.5 ** (days / 90.0)))
        else:
            recency = 50
        # Satisfaction
        sat_parts = []
        if vals.get('csat_latest'):
            sat_parts.append((vals['csat_latest'] - 1) / 4 * 100)
        if vals.get('nps_latest'):
            sat_parts.append(vals['nps_latest'])
        satisfaction = sum(sat_parts) / len(sat_parts) if sat_parts else 60.0
        # Renewal proximity
        renewal = vals.get('renewal_date')
        if not renewal:
            renewal_score = 100.0
        else:
            d = (renewal - fields.Date.context_today(self)).days
            renewal_score = 100 if d > 90 else 70 if d >= 30 else 40 if d >= 7 \
                else 20 if d >= 0 else 0
        # Usage
        usage_map = {'active': 100, 'low': 50, 'inactive': 0}
        usage = usage_map.get(self.usage_signal, 60)
        # AI sentiment (neutral 60 when no data / AI disabled)
        if vals.get('sentiment_label'):
            sentiment = clamp((vals.get('sentiment_score', 0) + 100) / 2.0)
        else:
            sentiment = 60.0

        score = round(
            self._cfg('weight_support', 0.18) * support
            + self._cfg('weight_collections', 0.12) * collections
            + self._cfg('weight_recency', 0.13) * recency
            + self._cfg('weight_satisfaction', 0.17) * satisfaction
            + self._cfg('weight_renewal', 0.12) * renewal_score
            + self._cfg('weight_usage', 0.13) * usage
            + self._cfg('weight_sentiment', 0.15) * sentiment)

        if score >= 75:
            status, color = 'good', 10
        elif score >= 55:
            status, color = 'watch', 3
        elif score >= 40:
            status, color = 'at_risk', 2
        else:
            status, color = 'critical', 1

        churn = bool(score < 40 or vals.get('subscription_churned'))
        return {
            'health_score': score,
            'health_status': status,
            'kanban_color': color,
            'churn_risk': churn,
        }

    # ==================================================================
    # Assignment helpers
    # ==================================================================
    @api.model
    def _least_loaded_csm(self, users):
        if not users:
            return False
        data = self._read_group(
            [('csm_user_id', 'in', users.ids), ('lifecycle_stage_id.is_churned', '=', False)],
            groupby=['csm_user_id'], aggregates=['__count'])
        load = {u.id: 0 for u in users}
        for user, count in data:
            load[user.id] = count
        return min(load, key=load.get)

    def action_auto_assign(self):
        team_members = self.env['res.users'].search(
            [('groups_id', 'in', self.env.ref('era_customer_success.group_era_cs_user').id)])
        for acc in self.filtered(lambda a: not a.csm_user_id):
            uid = acc._least_loaded_csm(team_members)
            if uid:
                acc.csm_user_id = uid
        return True

    # ==================================================================
    # Quick actions
    # ==================================================================
    def _act_window(self, name, res_model, view_mode='list,form', domain=None, context=None, target='current'):
        return {
            'type': 'ir.actions.act_window',
            'name': name,
            'res_model': res_model,
            'view_mode': view_mode,
            'domain': domain or [],
            'context': context or {},
            'target': target,
        }

    def action_cs_send_whatsapp(self):
        self.ensure_one()
        if 'whatsapp.composer' in self.env:
            return {
                'type': 'ir.actions.act_window',
                'name': _('Send WhatsApp'),
                'res_model': 'whatsapp.composer',
                'view_mode': 'form',
                'target': 'new',
                'context': {
                    'default_res_model': 'res.partner',
                    'default_res_ids': [self.partner_id.id],
                    'active_model': 'res.partner',
                    'active_id': self.partner_id.id,
                },
            }
        raise UserError(_('No WhatsApp channel is configured.'))

    def action_cs_send_sms(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Send SMS'),
            'res_model': 'sms.composer',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_res_model': 'res.partner',
                'default_res_ids': [self.partner_id.id],
                'default_composition_mode': 'comment',
                'active_model': 'res.partner',
                'active_id': self.partner_id.id,
            },
        }

    def action_account_copilot(self):
        """Open the AI copilot to ask natural-language questions about this customer."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Account Copilot'),
            'res_model': 'cs.account.copilot',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_cs_account_id': self.id,
                'default_partner_id': self.partner_id.id,
            },
        }

    def action_call_briefing(self):
        """Open the AI pre-call/meeting briefing (generated immediately) for this customer."""
        self.ensure_one()
        wiz = self.env['cs.call.briefing'].create({
            'cs_account_id': self.id,
            'partner_id': self.partner_id.id,
        })
        wiz._generate()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Pre-Call Briefing (AI)'),
            'res_model': 'cs.call.briefing',
            'res_id': wiz.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def action_compose_followup(self):
        """Open the AI follow-up / reply composer for this customer."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Compose Follow-up (AI)'),
            'res_model': 'cs.followup.compose',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_cs_account_id': self.id,
                'default_partner_id': self.partner_id.id,
            },
        }

    def action_cs_log_call(self):
        self.ensure_one()
        self.activity_schedule(
            act_type_xmlid='mail.mail_activity_data_call',
            date_deadline=fields.Date.context_today(self),
            summary=_('Call %s', self.partner_id.name),
            user_id=(self.csm_user_id or self.env.user).id)
        self.message_post(body=_('📞 Call scheduled for today.'))
        return True

    def action_cs_log_meeting(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Log Meeting'),
            'res_model': 'calendar.event',
            'view_mode': 'form',
            'target': 'current',
            'context': {
                'default_partner_ids': [(6, 0, [self.partner_id.id])],
                'default_user_id': (self.csm_user_id or self.env.user).id,
                'default_name': _('Follow-up meeting – %s', self.partner_id.name),
            },
        }

    def action_cs_schedule_followup(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Schedule Follow-up'),
            'res_model': 'mail.activity',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_res_model': 'cs.account',
                'default_res_id': self.id,
                'default_user_id': (self.csm_user_id or self.env.user).id,
            },
        }

    def action_open_success_profile(self):
        self.ensure_one()
        profile = self.success_profile_ids[:1]
        if not profile:
            profile = self.env['cs.success.profile'].create({
                'cs_account_id': self.id,
                'review_date': fields.Date.context_today(self) + timedelta(days=90),
            })
        return {
            'type': 'ir.actions.act_window',
            'name': _('Customer Success Plan'),
            'res_model': 'cs.success.profile',
            'res_id': profile.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_view_support_wallets(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Support Hours'),
            'res_model': 'cs.support.wallet',
            'view_mode': 'list,form',
            'domain': [('cs_account_id', '=', self.id)],
            'context': {'search_default_needs_attention': 1},
        }

    def action_view_value_reviews(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Value Reviews'),
            'res_model': 'cs.value.review',
            'view_mode': 'list,form',
            'domain': [('cs_account_id', '=', self.id)],
            'context': {
                'default_cs_account_id': self.id,
                'default_review_date': fields.Date.context_today(self),
            },
        }

    def action_view_adoption_assessments(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Adoption Assessments'),
            'res_model': 'cs.adoption.assessment',
            'view_mode': 'list,form',
            'domain': [('cs_account_id', '=', self.id)],
            'context': {'default_cs_account_id': self.id},
        }

    def action_view_voc_insights(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Voice of Customer'),
            'res_model': 'cs.voc.insight',
            'view_mode': 'list,form',
            'domain': [('cs_account_id', '=', self.id)],
            'context': {'default_cs_account_id': self.id},
        }

    def action_recommend_services(self):
        self.ensure_one()
        self.check_access('read')
        wizard = self.env['cs.service.recommendation.wizard'].create({
            'cs_account_id': self.id,
        })
        wizard.action_compute()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Recommended Services'),
            'res_model': 'cs.service.recommendation.wizard',
            'res_id': wizard.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def _refresh_adoption_work_item(self):
        worklist = self.env['cs.weekly.suggestion'].sudo()
        today = fields.Date.context_today(self)
        week = worklist._week_start(today)
        for account in self:
            values = account._daily_work_item_values(today)
            current = worklist.search([
                ('cs_account_id', '=', account.id),
                ('week', '=', week),
                ('state', '=', 'open'),
            ], limit=1)
            if values:
                worklist._upsert_automated_item(account, values, week=week)
            elif current and current.action_type == 'adoption':
                current.write({
                    'state': 'dismissed',
                    'outcome': 'not_relevant',
                    'outcome_note': _('The latest adoption evidence no longer requires this work item.'),
                    'completed_on': fields.Datetime.now(),
                    'completed_by_id': self.env.user.id,
                })

    def action_cs_present_offering(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Present New Offering'),
            'res_model': 'csm.offering',
            'view_mode': 'form',
            'target': 'current',
            'context': {
                'default_cs_account_id': self.id,
                'default_partner_id': self.partner_id.id,
                'default_csm_user_id': (self.csm_user_id or self.env.user).id,
            },
        }

    def action_cs_capture_request(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Capture Customer Request'),
            'res_model': 'cs.capture.request',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_cs_account_id': self.id,
                'default_partner_id': self.partner_id.id,
            },
        }

    # --- Smart buttons ---
    def action_view_tickets(self):
        self.ensure_one()
        return self._act_window(
            _('Tickets'), 'helpdesk.ticket', 'list,form',
            [('partner_id', 'in', self._partner_ids())],
            {'default_partner_id': self.partner_id.id})

    def action_view_subscriptions(self):
        self.ensure_one()
        return self._act_window(
            _('Subscriptions'), 'sale.order', 'list,form',
            [('partner_id', 'in', self._partner_ids()), ('is_subscription', '=', True)])

    def action_view_invoices(self):
        self.ensure_one()
        return self._act_window(
            _('Invoices'), 'account.move', 'list,form',
            [('partner_id', 'in', self._partner_ids()), ('move_type', '=', 'out_invoice')])

    def action_view_opportunities(self):
        self.ensure_one()
        return self._act_window(
            _('Opportunities'), 'crm.lead', 'list,form',
            [('partner_id', 'in', self._partner_ids()), ('type', '=', 'opportunity')],
            {'default_partner_id': self.partner_id.id, 'default_type': 'opportunity'})

    def action_view_calls(self):
        self.ensure_one()
        return self._act_window(
            _('Calls'), 'voip.call', 'list,form',
            [('partner_id', 'in', self._partner_ids())])

    def action_view_whatsapp(self):
        self.ensure_one()
        if hasattr(self.partner_id, 'action_open_partner_wa_channels'):
            return self.partner_id.action_open_partner_wa_channels()
        return self._act_window(
            _('WhatsApp'), 'discuss.channel', 'list,form',
            [('channel_type', '=', 'whatsapp'), ('channel_partner_ids', 'in', self._partner_ids())])

    def action_view_meetings(self):
        self.ensure_one()
        return self._act_window(
            _('Meetings'), 'calendar.event', 'list,form',
            [('partner_ids', 'in', self._partner_ids())])

    def action_view_surveys(self):
        self.ensure_one()
        return self._act_window(
            _('Surveys'), 'survey.user_input', 'list,form',
            [('partner_id', 'in', self._partner_ids())])

    def action_view_projects(self):
        self.ensure_one()
        return self._act_window(
            _('Projects'), 'project.project', 'kanban,list,form',
            [('partner_id', 'in', self._partner_ids())])

    def action_view_offerings(self):
        self.ensure_one()
        return self._act_window(
            _('Offerings'), 'csm.offering', 'list,form',
            [('cs_account_id', '=', self.id)],
            {'default_cs_account_id': self.id, 'default_partner_id': self.partner_id.id})

    # ==================================================================
    # Cron jobs
    # ==================================================================
    @api.model
    def _cron_recompute_metrics(self):
        accounts = self.search([])
        for batch in (accounts[i:i + 200] for i in range(0, len(accounts), 200)):
            batch._recompute_account_metrics()
            self.env.cr.commit()

    @api.model
    def _cron_advance_lifecycle(self):
        today = fields.Date.context_today(self)
        onboarding_stages = self.env['cs.stage'].search(
            [('is_onboarding', '=', True)], order='sequence')
        steady_stage = self.env.ref(
            'era_customer_success.cs_stage_steady', raise_if_not_found=False)
        risk_stage = self.env['cs.stage'].search([('is_at_risk', '=', True)], limit=1)
        for acc in self.search([('lifecycle_stage_id.is_onboarding', '=', True)]):
            if not acc.onboarding_start_date:
                continue
            elapsed = (today - acc.onboarding_start_date).days
            target = acc.lifecycle_stage_id
            for stage in onboarding_stages:
                if stage.day_window_end and elapsed >= stage.day_window_end:
                    nxt = onboarding_stages.filtered(lambda s: s.sequence > stage.sequence)[:1]
                    if nxt:
                        target = nxt
                    elif steady_stage:
                        target = steady_stage
            if target and target != acc.lifecycle_stage_id:
                acc.lifecycle_stage_id = target
        # Flag at-risk accounts
        if risk_stage:
            at_risk = self.search([
                '|', ('churn_risk', '=', True), ('churn_probability', '>=', 60),
                ('lifecycle_stage_id.is_onboarding', '=', False),
                ('lifecycle_stage_id.is_at_risk', '=', False),
                ('lifecycle_stage_id.is_churned', '=', False)])
            for acc in at_risk:
                acc.lifecycle_stage_id = risk_stage
                if acc.csm_user_id:
                    acc._auto_activity_schedule(
                        act_type_xmlid='mail.mail_activity_data_call',
                        summary=_('At-risk intervention – health dropped to %s', acc.health_score),
                        user_id=acc.csm_user_id.id)

    @api.model
    def _cron_renewal_and_escalation(self):
        today = fields.Date.context_today(self)
        # Renewal reminders at 90/60/30 days
        for window in (90, 60, 30):
            target = today + timedelta(days=window)
            accounts = self.search([
                ('renewal_date', '=', target), ('csm_user_id', '!=', False)])
            for acc in accounts:
                acc._auto_activity_schedule(
                    act_type_xmlid='mail.mail_activity_data_todo',
                    date_deadline=today,
                    summary=_('Renewal in %s days – prepare renewal for %s',
                              window, acc.partner_id.name),
                    user_id=acc.csm_user_id.id)
    @api.model
    def _cron_schedule_cadence(self):
        today = fields.Date.context_today(self)
        cadence_days = {'weekly': 7, 'biweekly': 14, 'monthly': 30, 'quarterly': 90}
        accounts = self.search([
            ('csm_user_id', '!=', False),
            ('lifecycle_stage_id.is_churned', '=', False)])
        for acc in accounts:
            interval = cadence_days.get(acc.cadence, 30)
            has_open = acc.activity_ids.filtered(lambda a: a.date_deadline)
            if has_open:
                continue
            last = acc.last_touch_date.date() if acc.last_touch_date else acc.onboarding_start_date
            if last and (today - last).days >= interval:
                acc._auto_activity_schedule(
                    act_type_xmlid='mail.mail_activity_data_call',
                    date_deadline=today,
                    summary=_('Scheduled %s follow-up', acc.cadence),
                    user_id=acc.csm_user_id.id)

    @api.model
    def _cron_sync_touchpoints(self, limit=500):
        """Mirror VoIP calls / closed tickets / done surveys into the account
        timeline (idempotent via cs_timeline_synced)."""
        self._sync_voip_calls(limit)
        self._sync_helpdesk_tickets(limit)
        self._sync_surveys(limit)

    def _account_for_partner(self, partner):
        if not partner:
            return self.env['cs.account']
        commercial = partner.commercial_partner_id or partner
        return self.search([('partner_id', '=', commercial.id)], limit=1)

    def _sync_voip_calls(self, limit):
        if 'voip.call' not in self.env:
            return
        Call = self.env['voip.call'].sudo()
        calls = Call.search(
            [('cs_timeline_synced', '=', False), ('partner_id', '!=', False),
             ('state', 'in', ('terminated', 'missed'))], limit=limit)
        # Only surface RECENT touch-points in the timeline; anything older is marked
        # synced silently so creating an account never dumps the whole history into
        # the chatter (the sync runs daily, so a 2-day window is plenty).
        cutoff = fields.Datetime.now() - timedelta(days=_CS_SYNC_RECENCY_DAYS)
        for call in calls:
            acc = self._account_for_partner(call.partner_id)
            if acc and call.start_date and call.start_date >= cutoff:
                summary = call.summary if 'summary' in call._fields and call.summary else ''
                acc.message_post(
                    body=Markup(_('📞 <b>Call</b> (%(dir)s) – %(mins).0f min. %(sum)s',
                                  dir=call.direction or '', mins=(call.duration or 0) * 60,
                                  sum=summary)),
                    subtype_xmlid='era_customer_success.mt_cs_call')
            call.cs_timeline_synced = True

    def _sync_helpdesk_tickets(self, limit):
        Ticket = self.env['helpdesk.ticket'].sudo()
        tickets = Ticket.search(
            [('cs_timeline_synced', '=', False), ('partner_id', '!=', False),
             ('stage_id.fold', '=', True)], limit=limit)
        cutoff = fields.Datetime.now() - timedelta(days=_CS_SYNC_RECENCY_DAYS)
        for tk in tickets:
            acc = self._account_for_partner(tk.partner_id)
            if acc and tk.close_date and tk.close_date >= cutoff:
                # Factual touch-point only — no AI sentiment emoji in the timeline.
                acc.message_post(
                    body=Markup(_('🎫 <b>Ticket closed</b>: %(name)s (resolution %(h).1f h)',
                                  name=tk.name, h=tk.close_hours or 0.0)),
                    subtype_xmlid='era_customer_success.mt_cs_ticket')
            tk.cs_timeline_synced = True

    def _sync_surveys(self, limit):
        SurveyInput = self.env['survey.user_input'].sudo()
        inputs = SurveyInput.search(
            [('cs_timeline_synced', '=', False), ('partner_id', '!=', False),
             ('state', '=', 'done')], limit=limit)
        cutoff = fields.Datetime.now() - timedelta(days=_CS_SYNC_RECENCY_DAYS)
        for inp in inputs:
            acc = self._account_for_partner(inp.partner_id)
            if acc and inp.create_date and inp.create_date >= cutoff:
                acc.message_post(
                    body=Markup(_('📊 <b>Survey response</b>: %(s)s – score %(p).0f%%',
                                  s=inp.survey_id.title, p=inp.scoring_percentage or 0.0)),
                    subtype_xmlid='era_customer_success.mt_cs_survey')
                if (inp.scoring_percentage or 0) < 60 and acc.csm_user_id:
                    acc._auto_activity_schedule(
                        act_type_xmlid='mail.mail_activity_data_call',
                        summary=_('Service recovery – low survey score'),
                        user_id=acc.csm_user_id.id)
            inp.cs_timeline_synced = True

    # ==================================================================
    # AI – Profile summary (under the customer name)
    # ==================================================================
    def _build_profile_context(self):
        self.ensure_one()
        pids = self._partner_ids()
        today = fields.Date.context_today(self)
        Ticket = self.env['helpdesk.ticket'].sudo()
        Project = self.env['project.project'].sudo() if 'project.project' in self.env.registry else None
        Sale = self.env['sale.order'].sudo()

        dates = []
        if self.onboarding_start_date:
            dates.append(self.onboarding_start_date)
        if self.partner_id.create_date:
            dates.append(self.partner_id.create_date.date())
        first_ticket = Ticket.search([('partner_id', 'in', pids)], order='create_date asc', limit=1)
        if first_ticket.create_date:
            dates.append(first_ticket.create_date.date())
        if Project is not None:
            first_project = Project.search([('partner_id', 'in', pids)], order='create_date asc', limit=1)
            if first_project.create_date:
                dates.append(first_project.create_date.date())
        since = min(dates) if dates else today
        months = (today.year - since.year) * 12 + (today.month - since.month)

        project_names = Project.search([('partner_id', 'in', pids)], limit=8).mapped('name') \
            if Project is not None else []
        subs = Sale.search([('partner_id', 'in', pids), ('is_subscription', '=', True)])
        sub_products = subs.order_line.product_id.mapped('name')[:8] if subs else []
        tags = self.partner_id.category_id.mapped('name')

        L = [
            "Customer: %s" % self.partner_id.name,
            "Industry / field of work: %s" % (
                self.partner_id.industry_id.name if self.partner_id.industry_id else '-'),
            "Tags: %s" % (', '.join(tags) if tags else '-'),
            "Customer since: %s (~%s months)" % (since, max(0, months)),
            "Lifecycle: %s | Health: %s/100 (%s) | MRR: %s" % (
                self.lifecycle_stage_id.name or '-', self.health_score, self.health_status, self.mrr),
            "Engagement scope: %s tickets, %s projects, %s subscriptions" % (
                self.ticket_total_count, self.project_count, self.subscription_count),
        ]
        if project_names:
            L.append("Projects: " + "; ".join(project_names))
        if sub_products:
            L.append("Subscribed products: " + "; ".join(sub_products))
        return "\n".join(L)

    def action_generate_summary(self):
        self.check_access('read')
        if any(not account.company_id.cs_ai_summary_enabled for account in self):
            raise UserError(_('Enable AI Profile Summary in Customer Success settings first.'))
        agent = self.env.ref('era_customer_success.cs_profile_summary_agent', raise_if_not_found=False)
        if not agent:
            raise UserError(_('The profile-summary AI agent is not available.'))
        root = self.env.ref('base.user_root')
        for acc in self:
            try:
                response = agent.with_user(root).get_direct_response(prompt=acc._build_profile_context())
                text = (response[0] if response else '') or ''
            except Exception as e:
                _logger.warning("CS profile summary skipped for account %s (AI error): %s", acc.id, e)
                continue
            if text.strip():
                acc.write({'ai_summary': text.strip(), 'ai_summary_date': fields.Datetime.now()})
        return True

    @api.model
    def _cron_generate_summaries(self, limit=15):
        """Gradually generate the AI profile summary for all accounts (opt-in)."""
        company_ids = self._ai_enabled_company_ids('cs_ai_summary_enabled')
        if not company_ids:
            return
        stale = fields.Datetime.now() - timedelta(days=30)
        accounts = self.search([
            ('company_id', 'in', company_ids),
            '|', ('ai_summary', '=', False), ('ai_summary_date', '<', stale),
        ], order='ai_summary_date asc, id asc', limit=limit)
        if accounts:
            self._run_ai_cron_per_account(accounts, 'action_generate_summary')

    # ==================================================================
    # Shared AI context builders (reused by churn/renewal/QBR/digest…)
    # ==================================================================
    def _build_snapshot_trend(self, limit=8):
        """Serialise the last N monthly KPI snapshots into a compact table for AI."""
        self.ensure_one()
        snaps = self.env['csm.kpi.snapshot'].sudo().search(
            [('cs_account_id', '=', self.id)], order='period_start desc', limit=limit)
        if not snaps:
            return "No historical KPI snapshots yet (new account)."
        rows = ["period | health | mrr | csat(1-5) | nps% | open_tickets | sentiment | days_since_touch | churned"]
        for s in reversed(snaps):
            rows.append("%s | %s | %s | %s | %s | %s | %s | %s | %s" % (
                s.period_start, s.health_score, int(s.mrr or 0), round(s.csat or 0, 1),
                round(s.nps or 0, 1), s.open_tickets, s.sentiment, s.days_since_touch,
                'yes' if s.churned else 'no'))
        return "\n".join(rows)

    # ==================================================================
    # AI – Churn probability forecast (predictive)
    # ==================================================================
    def _churn_floor(self):
        """Hard-signal floor so a hallucinated low probability can't hide real risk."""
        self.ensure_one()
        floor = 0
        if self.subscription_churned:
            floor = 100
        if self.overdue_amount and self.mrr and self.overdue_amount > self.mrr:
            floor = max(floor, 75)
        if self.sla_failed_count:
            floor = max(floor, 55)
        if self.health_status == 'critical':
            floor = max(floor, 65)
        elif self.health_status == 'at_risk':
            floor = max(floor, 45)
        if self.sentiment_label == 'negative':
            floor = max(floor, 50)
        return floor

    def action_forecast_churn(self):
        """AI churn probability at 30/60/90 days, grounded on situation + snapshot history."""
        agent = self.env.ref('era_customer_success.cs_churn_forecast_agent', raise_if_not_found=False)
        if not agent:
            raise UserError(_('The churn-forecast AI agent is not available.'))
        root = self.env.ref('base.user_root')

        def _pct(v):
            try:
                return max(0, min(100, int(round(float(v)))))
            except (TypeError, ValueError):
                return 0

        for acc in self:
            # Whole body guarded: one malformed AI reply must not abort the batch.
            try:
                prompt = "%s\n\n=== HISTORICAL KPI TREND (oldest → newest) ===\n%s" % (
                    acc._build_situation_summary(), acc._build_snapshot_trend())
                response = agent.with_user(root).get_direct_response(prompt=prompt)
                data = _cs_extract_json(response[0] if response else '')
                if not isinstance(data, dict):
                    continue
                p30, p60, p90 = _pct(data.get('p30')), _pct(data.get('p60')), _pct(data.get('p90'))
                conf = (data.get('confidence') or 'medium').lower()
                if conf not in ('low', 'medium', 'high'):
                    conf = 'medium'
                factors = data.get('top_factors')
                if isinstance(factors, list):
                    factors = "\n".join("- %s" % f for f in factors if f)
                elif not isinstance(factors, str):
                    factors = ''
                # apply hard-signal floor + keep monotonic non-decreasing
                floor = acc._churn_floor()
                p90 = max(p90, floor)
                p60 = max(p60, int(floor * 0.85))
                p30 = max(p30, int(floor * 0.65))
                p60 = max(p60, p30)
                p90 = max(p90, p60)
                acc.write({
                    'churn_probability': p90,
                    'churn_prob_30': p30,
                    'churn_prob_60': p60,
                    'churn_confidence': conf,
                    'churn_factors': (factors or '')[:2000],
                    'churn_forecast_date': fields.Datetime.now(),
                })
            except Exception as e:
                # transient AI/provider errors are expected and handled (account retried next run)
                _logger.warning("CS churn forecast skipped for account %s (AI error): %s", acc.id, e)
                continue
        return True

    def _run_ai_cron_per_account(self, records, method_name):
        """Run a slow per-record AI method (churn/renewal/next-action/summary on
        cs.account, sentiment on helpdesk.ticket) ONE record per transaction, committing
        after each.

        Critical for responsiveness: each AI call keeps the transaction open for many
        seconds (up to the provider timeout). Processing the whole batch in one
        transaction kept ROW LOCKS held on every touched cs_account/ticket row for the
        full run (up to the worker time-limit, ~20 min) — any UI edit or other cron
        touching those rows then blocked, so the system looked frozen during cron runs.
        It also raced concurrent writes (SerializationFailure) and, on timeout, rolled
        back the WHOLE batch. Committing per record releases locks within seconds, saves
        progress, and isolates a conflict to a single record (retried next run).
        """
        for rec in records:
            try:
                getattr(rec, method_name)()
                self.env.cr.commit()
            except psycopg2_errors.SerializationFailure:
                self.env.cr.rollback()
                _logger.warning(
                    "CS AI cron %s: serialization conflict on record %s; will retry next run.",
                    method_name, rec.id)
            except Exception:
                self.env.cr.rollback()
                _logger.exception("CS AI cron %s failed for record %s", method_name, rec.id)

    @api.model
    def _cron_forecast_churn(self, batch=5):
        """Forecast churn for ALL active accounts (cost is not a constraint; accuracy first).

        One account per transaction (see _run_ai_cron_per_account) so a concurrent-update
        serialization conflict can't roll back a long multi-account run. `batch` is kept for
        backward compatibility with the cron action signature and is no longer used.
        """
        company_ids = self._ai_enabled_company_ids('cs_ai_churn_enabled')
        if not company_ids:
            return
        accounts = self.search([('company_id', 'in', company_ids),
                                ('lifecycle_stage_id.is_churned', '=', False)],
                               order='health_score asc, id asc')
        self._run_ai_cron_per_account(accounts, 'action_forecast_churn')

    # ==================================================================
    # AI – Renewal strategy (which lever to pull, window-gated)
    # ==================================================================
    def action_renewal_strategy(self):
        """AI renewal play: which lever + concrete countdown steps for accounts near renewal."""
        agent = self.env.ref('era_customer_success.cs_renewal_strategy_agent', raise_if_not_found=False)
        if not agent:
            raise UserError(_('The renewal-strategy AI agent is not available.'))
        root = self.env.ref('base.user_root')
        valid_levers = ('pitch_first', 'collections_first', 're_engage', 'standard')
        valid_risk = ('likely', 'at_risk', 'critical')
        for acc in self:
            # Whole body guarded: one malformed AI reply must not abort the batch.
            try:
                prompt = (
                    "%s\n\n=== HISTORICAL KPI TREND ===\n%s\n\n"
                    "Renewal is in %s days (date %s). Overdue: %s. Churn probability 90d: %s%%. "
                    "Recommend the renewal play." % (
                        acc._build_situation_summary(), acc._build_snapshot_trend(),
                        acc.days_to_renewal, acc.renewal_date or '-', acc.overdue_amount,
                        acc.churn_probability))
                response = agent.with_user(root).get_direct_response(prompt=prompt)
                data = _cs_extract_json(response[0] if response else '')
                if not isinstance(data, dict):
                    continue
                lever = (data.get('lever') or 'standard').lower()
                if lever not in valid_levers:
                    lever = 'standard'
                risk = (data.get('risk') or 'likely').lower()
                if risk not in valid_risk:
                    risk = 'likely'
                steps = data.get('strategy')
                if isinstance(steps, list):
                    steps = "\n".join("- %s" % s for s in steps if s)
                elif not isinstance(steps, str):
                    steps = ''
                acc.write({
                    'renewal_lever': lever,
                    'renewal_risk': risk,
                    'renewal_strategy': (steps or '')[:3000],
                    'renewal_strategy_date': fields.Datetime.now(),
                })
                if acc.csm_user_id and risk in ('at_risk', 'critical'):
                    # Cap at one open "Renewal play" activity per account. This cron
                    # re-runs daily over every renewal-soon account, so without this
                    # guard it would schedule a fresh activity every single day.
                    marker = _('Renewal play (%s) – %s', '\x00', '\x00').split('\x00')[0]
                    if not acc.activity_ids.filtered(
                            lambda a: a.summary and a.summary.startswith(marker)):
                        acc._auto_activity_schedule(
                            act_type_xmlid='mail.mail_activity_data_call',
                            summary=_('Renewal play (%s) – %s', risk, acc.partner_id.name),
                            user_id=acc.csm_user_id.id)
            except Exception as e:
                _logger.warning("CS renewal strategy skipped for account %s (AI error): %s", acc.id, e)
                continue
        return True

    @api.model
    def _cron_renewal_strategy(self, batch=5):
        """Generate the renewal play for ALL accounts inside the renewal window (<=90d).

        One account per transaction (see _run_ai_cron_per_account) to avoid a
        concurrent-update serialization conflict aborting the whole run. `batch` is kept
        for backward compatibility with the cron action signature and is no longer used.
        """
        company_ids = self._ai_enabled_company_ids('cs_ai_renewal_enabled')
        if not company_ids:
            return
        accounts = self.search([
            ('company_id', 'in', company_ids),
            ('renewal_soon', '=', True),
            ('lifecycle_stage_id.is_churned', '=', False),
        ], order='renewal_date asc, id asc')
        self._run_ai_cron_per_account(accounts, 'action_renewal_strategy')

    # ==================================================================
    # AI – Weekly per-CSM suggestions (structured worklist records)
    # ==================================================================
    def _build_csm_worklist_prompt(self, csm, accs):
        """Indexed signal table of a CSM's portfolio for the worklist agent."""
        lines = []
        for i, a in enumerate(accs):
            lines.append(
                "[%s] %s | health=%s | churn90=%s%% | renewal_in=%s | days_since_touch=%s | "
                "open_tickets=%s | sentiment=%s | mrr=%s" % (
                    i, a.partner_id.name or '?', a.health_score, a.churn_probability,
                    (a.days_to_renewal if a.renewal_date else '-'), a.days_since_touch,
                    a.open_tickets_count, a.sentiment_label or '-', int(a.mrr or 0)))
        return "CSM: %s\nCustomers (%s):\n%s\n\nReturn this week's ranked worklist as JSON." % (
            csm.name, len(accs), "\n".join(lines))

    def _daily_work_item_values(self, today=None):
        """Return the single highest-value intervention for this account today."""
        self.ensure_one()
        today = today or fields.Date.context_today(self)
        if self.subscription_churned or (self.lifecycle_stage_id and self.lifecycle_stage_id.is_churned):
            return False
        if self.health_status == 'critical' or self.churn_probability >= 60:
            return {
                'source': 'automation',
                'action_type': 'risk_recovery',
                'priority': 'urgent',
                'rank': 1,
                'due_date': today,
                'reason': _('Critical customer health or high churn probability requires immediate attention.'),
                'recommended_action': _('Contact the customer, identify the immediate cause, and agree on a dated recovery step.'),
            }
        if self.sentiment_label == 'negative' or self.sla_failed_count:
            return {
                'source': 'automation',
                'action_type': 'support_recovery',
                'priority': 'high',
                'rank': 2,
                'due_date': today,
                'reason': _('Negative sentiment or a failed support SLA may damage customer trust.'),
                'recommended_action': _('Review the support issue, contact the customer with a clear update, and record the recovery commitment.'),
            }
        voc = self.env['cs.voc.insight'].sudo().search([
            ('cs_account_id', '=', self.id),
            ('state', 'in', ('new', 'triaged')),
            ('priority', '=', 'high'),
        ], order='insight_date, id', limit=1)
        if voc:
            return {
                'source': 'automation',
                'action_type': 'voice_customer',
                'priority': 'high',
                'rank': 3,
                'due_date': today,
                'voc_id': voc.id,
                'reason': _('High-priority customer voice: %s', voc.name),
                'recommended_action': _(
                    'Review the customer evidence, agree a response, and record the action taken.'),
            }
        wallets = self.env['cs.support.wallet'].sudo().search([
            ('cs_account_id', '=', self.id),
        ], order='order_date desc, id desc')
        active_wallets = wallets.filtered(
            lambda item: item.expiry_date >= today and item.remaining_hours > 0)
        candidates = (
            active_wallets.filtered('attention_rank')
            if active_wallets else wallets.filtered('attention_rank'))
        wallet = max(candidates, key=lambda item: item.attention_rank, default=False)
        if wallet:
            urgent = wallet.status in ('exhausted', 'expired')
            return {
                'source': 'automation',
                'action_type': 'support_hours',
                'priority': 'high' if urgent else 'medium',
                'rank': 4,
                'due_date': today,
                'reason': _(
                    'Support package "%(package)s" has %(remaining).1f hours remaining '
                    '(%(percent).0f%%); status: %(status)s.',
                    package=wallet.product_id.display_name,
                    remaining=wallet.remaining_hours,
                    percent=wallet.remaining_percentage,
                    status=wallet.status),
                'recommended_action': _(
                    'Review delivered support value and upcoming needs with the customer. '
                    'Create a qualified support-hours need only after confirming interest.'),
            }
        if self.renewal_date and 0 <= self.days_to_renewal <= 90:
            return {
                'source': 'automation',
                'action_type': 'renewal',
                'priority': 'high' if self.days_to_renewal <= 30 else 'medium',
                'rank': 5,
                'due_date': today,
                'reason': _('The customer is within the renewal attention window.'),
                'recommended_action': _('Review delivered value and customer concerns before handing any commercial need to the responsible team.'),
            }
        milestone = self.env['cs.success.milestone'].sudo().search([
            ('cs_account_id', '=', self.id),
            ('profile_id.state', '=', 'active'),
            ('state', 'not in', ('achieved', 'cancelled')),
            ('target_date', '<=', today + timedelta(days=7)),
        ], order='target_date, attention_rank desc, id', limit=1)
        if milestone:
            overdue = milestone.target_date < today
            return {
                'source': 'automation',
                'action_type': 'success_milestone',
                'priority': 'urgent' if milestone.state == 'blocked' else (
                    'high' if overdue else milestone.priority),
                'rank': 6,
                'due_date': min(milestone.target_date, today),
                'reason': _(
                    'Success milestone "%(milestone)s" is %(timing)s.',
                    milestone=milestone.name,
                    timing=_('overdue') if overdue else _('due within 7 days')),
                'recommended_action': _(
                    'Move the milestone forward, record evidence or blockers, and agree the next customer step.'),
            }
        value_review = self.env['cs.value.review'].sudo().search([
            ('cs_account_id', '=', self.id),
            ('state', 'in', ('draft', 'prepared', 'held')),
            ('review_date', '<=', today + timedelta(days=14)),
        ], order='review_date, id', limit=1)
        if value_review:
            overdue = value_review.review_date <= today
            return {
                'source': 'automation',
                'action_type': 'value_review',
                'priority': 'high' if overdue else 'medium',
                'rank': 7,
                'due_date': min(value_review.review_date, today),
                'reason': _(
                    'Customer value review "%(review)s" is %(timing)s.',
                    review=value_review.name,
                    timing=_('due or overdue') if overdue else _('due within 14 days')),
                'recommended_action': _(
                    'Prepare the evidence and objectives, hold the customer review, and record confirmed value and commitments.'),
            }
        adoption = self.env['cs.adoption.assessment'].sudo().search([
            ('cs_account_id', '=', self.id),
            ('state', '=', 'confirmed'),
        ], order='assessment_date desc, id desc', limit=1)
        if adoption and (
                adoption.status in ('watch', 'low')
                or (adoption.next_assessment_date and adoption.next_assessment_date <= today)):
            assessment_due = bool(
                adoption.next_assessment_date and adoption.next_assessment_date <= today)
            low_confidence = adoption.confidence < 50
            return {
                'source': 'automation',
                'action_type': 'adoption',
                'priority': 'high' if adoption.status == 'low' and not low_confidence else 'medium',
                'rank': 8,
                'due_date': today,
                'reason': _(
                    'Adoption is %(status)s at %(score).0f%% with %(confidence).0f%% data confidence%(due)s.',
                    status=adoption.status,
                    score=adoption.score,
                    confidence=adoption.confidence,
                    due=_('; a new assessment is due') if assessment_due else ''),
                'recommended_action': _(
                    'Validate the adoption data first, then confirm blockers and agree a targeted enablement action.'
                    if low_confidence else
                    'Validate the adoption blockers and agree a targeted enablement action with the customer.'),
            }
        cadence_days = {'weekly': 7, 'biweekly': 14, 'monthly': 30, 'quarterly': 90}
        overdue_days = cadence_days.get(self.cadence, 30)
        if not self.last_touch_date or self.days_since_touch >= overdue_days:
            return {
                'source': 'automation',
                'action_type': 'relationship',
                'priority': 'high' if self.days_since_touch >= overdue_days * 2 else 'medium',
                'rank': 9,
                'due_date': today,
                'reason': _('Customer contact is missing or overdue for the agreed follow-up cadence.'),
                'recommended_action': _('Make a value-led check-in, confirm current priorities, and agree on the next contact date.'),
            }
        if self.usage_signal in ('low', 'inactive'):
            return {
                'source': 'automation',
                'action_type': 'value',
                'priority': 'medium',
                'rank': 10,
                'due_date': today,
                'reason': _('Low system usage indicates that the customer may not be realizing enough value.'),
                'recommended_action': _('Identify the adoption blocker and offer a targeted enablement, training, or support action.'),
            }
        return False

    def _generate_weekly_suggestions(self, week=None):
        """Regenerate each CSM's weekly suggestion records. Returns (csms, items) counts."""
        agent = self.env.ref('era_customer_success.cs_worklist_agent', raise_if_not_found=False)
        if not agent:
            raise UserError(_('The weekly-worklist AI agent is not available.'))
        root = self.env.ref('base.user_root')
        Sugg = self.env['cs.weekly.suggestion'].sudo()
        valid_prio = ('urgent', 'high', 'medium', 'low')
        week = week or fields.Date.context_today(self)
        company_ids = self._ai_enabled_company_ids('cs_ai_digest_enabled')
        if not company_ids:
            return 0, 0
        csms = self.search([
            ('company_id', 'in', company_ids),
            ('csm_user_id', '!=', False),
            ('lifecycle_stage_id.is_churned', '=', False)]).mapped('csm_user_id')
        n_csm, n_items = 0, 0
        for csm in csms:
            accs = self.search([
                ('company_id', 'in', company_ids),
                ('csm_user_id', '=', csm.id),
                ('lifecycle_stage_id.is_churned', '=', False)])
            if not accs:
                continue
            try:
                response = agent.with_user(root).get_direct_response(
                    prompt=self._build_csm_worklist_prompt(csm, accs))
                data = _cs_extract_json(response[0] if response else '')
            except Exception as e:
                _logger.warning("CS weekly suggestions skipped for CSM %s (AI error): %s", csm.id, e)
                continue
            if not isinstance(data, dict):
                continue
            items = data.get('items') or []
            for rank, it in enumerate(items):
                if not isinstance(it, dict):
                    continue
                try:
                    idx = int(it.get('index'))
                except (TypeError, ValueError):
                    continue
                if idx < 0 or idx >= len(accs):
                    continue
                acc = accs[idx]
                prio = (it.get('priority') or 'medium').lower()
                if prio not in valid_prio:
                    prio = 'medium'
                Sugg._upsert_automated_item(acc, {
                    'source': 'ai',
                    'rank': rank,
                    'priority': prio,
                    'action_type': 'relationship',
                    'due_date': week + timedelta(days=4),
                    'reason': (it.get('reason') or '')[:1000],
                    'recommended_action': (it.get('action') or '')[:1000],
                }, week=week)
                n_items += 1
            n_csm += 1
            self.env.cr.commit()
        return n_csm, n_items

    @api.model
    def _cron_weekly_digest(self):
        """Weekly: regenerate each CSM's ranked worklist of accounts needing attention (opt-in).

        The generation makes one slow AI call per CSM, so we run it in a DETACHED
        background thread and return immediately — the cron worker is never blocked
        (which previously froze other crons / the UI for the whole run). The thread
        keeps its own cursor and commits per CSM, so progress survives an interruption,
        and a session advisory lock prevents two runs from overlapping.
        """
        if not self._ai_enabled_company_ids('cs_ai_digest_enabled'):
            return
        threading.Thread(
            target=self._weekly_digest_detached,
            name='cs_weekly_digest', daemon=True).start()

    def _weekly_digest_detached(self):
        """Background worker for the weekly worklist — runs on its OWN cursor, never the
        cron's (which is closed as soon as _cron_weekly_digest returns)."""
        _logger.info("CS weekly digest: starting detached background generation.")
        try:
            with self.env.registry.cursor() as cr:
                cr.execute("SELECT pg_try_advisory_lock(%s)", (_CS_WEEKLY_DIGEST_LOCK,))
                if not cr.fetchone()[0]:
                    _logger.info("CS weekly digest: a run is already in progress; skipping.")
                    return
                try:
                    env = api.Environment(cr, SUPERUSER_ID, {})
                    n_csm, n_items = env['cs.account']._generate_weekly_suggestions()
                    _logger.info(
                        "CS weekly digest: detached run finished (%s CSMs, %s items).",
                        n_csm, n_items)
                finally:
                    cr.execute("SELECT pg_advisory_unlock(%s)", (_CS_WEEKLY_DIGEST_LOCK,))
        except Exception:
            _logger.exception("CS weekly digest: detached background run failed.")

    @api.model
    def action_run_weekly_digest_now(self):
        """Manager-triggered: regenerate the weekly suggestions immediately."""
        n_csm, n_items = self._generate_weekly_suggestions()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'type': 'success',
                'message': _('Generated %(items)s weekly suggestions for %(csms)s CSM(s).',
                             items=n_items, csms=n_csm),
                'next': {'type': 'ir.actions.act_window_close'},
                'sticky': False,
            },
        }

    @api.model
    def _cron_analyze_sentiment(self, limit=20):
        """Analyse sentiment of unprocessed tickets for managed customers (opt-in)."""
        company_ids = self._ai_enabled_company_ids('cs_ai_sentiment_enabled')
        if not company_ids:
            return
        tickets = self.env['helpdesk.ticket'].sudo().search([
            ('cs_sentiment_analyzed', '=', False),
            ('partner_id', '!=', False),
            ('partner_id.cs_account_id', '!=', False),
            ('partner_id.cs_account_id.company_id', 'in', company_ids),
        ], order='create_date desc', limit=limit)
        if tickets:
            self._run_ai_cron_per_account(tickets, '_cs_analyze_sentiment')

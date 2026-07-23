# -*- coding: utf-8 -*-
import logging
from datetime import timedelta

from psycopg2 import IntegrityError

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError

from .cs_account import _cs_extract_json

_logger = logging.getLogger(__name__)


class CsValueReview(models.Model):
    _name = 'cs.value.review'
    _description = 'Customer Value Review'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'review_date desc, id desc'
    _check_company_auto = True

    name = fields.Char(required=True, tracking=True)
    cs_account_id = fields.Many2one(
        'cs.account', string='Customer', required=True, ondelete='cascade',
        check_company=True, index=True, tracking=True)
    partner_id = fields.Many2one(
        related='cs_account_id.partner_id', string='Customer Company', store=True)
    company_id = fields.Many2one(
        related='cs_account_id.company_id', store=True, index=True)
    csm_user_id = fields.Many2one(
        related='cs_account_id.csm_user_id', string='CSM Engineer', store=True, index=True)
    state = fields.Selection([
        ('draft', 'Draft'),
        ('prepared', 'Prepared'),
        ('held', 'Held'),
        ('closed', 'Closed'),
        ('cancelled', 'Cancelled'),
    ], default='draft', required=True, tracking=True, index=True)
    review_date = fields.Date(required=True, default=fields.Date.context_today, tracking=True, index=True)
    period_start = fields.Date(required=True)
    period_end = fields.Date(required=True)
    next_review_date = fields.Date(string='Next Value Review')
    stakeholder_ids = fields.Many2many(
        'cs.success.stakeholder', 'cs_value_review_stakeholder_rel',
        'review_id', 'stakeholder_id', string='Participants')

    snapshot_captured_on = fields.Datetime(readonly=True, copy=False)
    health_score_snapshot = fields.Integer(string='Health Score', readonly=True)
    csat_snapshot = fields.Float(string='CSAT', readonly=True)
    nps_snapshot = fields.Float(string='Survey Score', readonly=True)
    sentiment_snapshot = fields.Integer(string='Sentiment', readonly=True)
    open_tickets_snapshot = fields.Integer(string='Open Tickets', readonly=True)
    sla_failed_snapshot = fields.Integer(string='Failed SLA', readonly=True)
    support_purchased_snapshot = fields.Float(string='Support Hours Purchased', readonly=True)
    support_used_snapshot = fields.Float(string='Support Hours Used', readonly=True)
    support_remaining_snapshot = fields.Float(string='Support Hours Remaining', readonly=True)
    objectives_snapshot = fields.Text(string='Customer Objectives', readonly=True)
    success_criteria_snapshot = fields.Text(string='Success Criteria', readonly=True)
    milestones_snapshot = fields.Text(string='Milestone Progress', readonly=True)
    support_snapshot = fields.Text(string='Support Summary', readonly=True)

    agenda = fields.Text()
    data_observations = fields.Text(string='Data Observations')
    discussion_questions = fields.Text(string='Questions to Discuss')
    value_realized = fields.Text(string='Customer-Confirmed Value')
    evidence = fields.Text(string='Evidence')
    risks_and_blockers = fields.Text(string='Risks and Blockers')
    customer_priorities = fields.Text(string='Customer Priorities')
    commitments = fields.Text(string='Agreed Commitments')
    potential_needs = fields.Text(
        string='Needs to Validate',
        help='Potential customer needs discovered during the review. This does not create a commercial opportunity.')
    next_step = fields.Char(string='Next Step')
    next_step_date = fields.Date(string='Next Step Date')

    ai_generated_on = fields.Datetime(string='AI Draft Generated On', readonly=True, copy=False)
    ai_value_review_enabled = fields.Boolean(
        related='company_id.cs_ai_value_review_enabled', readonly=True)
    prepared_on = fields.Datetime(readonly=True, copy=False)
    prepared_by_id = fields.Many2one('res.users', readonly=True, copy=False)
    held_on = fields.Datetime(readonly=True, copy=False)
    closed_on = fields.Datetime(readonly=True, copy=False)

    _account_period_unique = models.Constraint(
        'unique(cs_account_id, period_start, period_end)',
        'A value review already exists for this customer and period.')

    _snapshot_fields = {
        'cs_account_id', 'review_date', 'period_start', 'period_end',
        'snapshot_captured_on', 'health_score_snapshot', 'csat_snapshot',
        'nps_snapshot', 'sentiment_snapshot', 'open_tickets_snapshot',
        'sla_failed_snapshot', 'support_purchased_snapshot',
        'support_used_snapshot', 'support_remaining_snapshot',
        'objectives_snapshot', 'success_criteria_snapshot',
        'milestones_snapshot', 'support_snapshot',
    }

    @api.model_create_multi
    def create(self, vals_list):
        today = fields.Date.context_today(self)
        for vals in vals_list:
            if not self.env.su and (
                    vals.get('state', 'draft') != 'draft'
                    or self._snapshot_fields.intersection(vals)):
                raise UserError(_(
                    'Create value reviews as drafts; Prepare Review captures the snapshot.'))
            review_date = fields.Date.to_date(vals.get('review_date')) or today
            vals.setdefault('period_end', review_date)
            vals.setdefault('period_start', review_date - timedelta(days=90))
            if not vals.get('name') and vals.get('cs_account_id'):
                account = self.env['cs.account'].browse(vals['cs_account_id'])
                vals['name'] = _('Value Review - %(customer)s - %(date)s',
                                 customer=account.partner_id.name, date=review_date)
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su and 'state' in vals:
            raise UserError(_('Use the value-review workflow buttons to change its status.'))
        if not self.env.su and self._snapshot_fields.intersection(vals):
            raise UserError(_('Value-review snapshot fields can only be captured by Prepare Review.'))
        if self.filtered('snapshot_captured_on') and self._snapshot_fields.intersection(vals):
            raise UserError(_('The prepared value-review snapshot cannot be modified.'))
        return super().write(vals)

    def _refresh_daily_work_item(self):
        worklist = self.env['cs.weekly.suggestion'].sudo()
        today = fields.Date.context_today(self)
        week = worklist._week_start(today)
        for account in self.mapped('cs_account_id'):
            current = worklist.search([
                ('cs_account_id', '=', account.id),
                ('week', '=', week),
                ('state', '=', 'open'),
            ], limit=1)
            values = account._daily_work_item_values(today)
            if values:
                worklist._upsert_automated_item(account, values, week=week)
            elif current and current.action_type == 'value_review':
                current.write({
                    'state': 'dismissed',
                    'outcome': 'not_relevant',
                    'outcome_note': _('The value review no longer requires this work item.'),
                    'completed_on': fields.Datetime.now(),
                    'completed_by_id': self.env.user.id,
                })

    @api.constrains('period_start', 'period_end', 'review_date')
    def _check_dates(self):
        for review in self:
            if review.period_start > review.period_end:
                raise ValidationError(_('The review period start must be before its end.'))
            if review.review_date < review.period_end:
                raise ValidationError(_('The review date cannot be before the reviewed period ends.'))

    @api.constrains('stakeholder_ids', 'cs_account_id')
    def _check_stakeholders(self):
        for review in self:
            invalid = review.stakeholder_ids.filtered(
                lambda stakeholder: stakeholder.cs_account_id != review.cs_account_id)
            if invalid:
                raise ValidationError(_('All participants must belong to the reviewed customer.'))

    def _snapshot_values(self):
        self.ensure_one()
        account = self.cs_account_id
        profile = account.success_profile_ids.filtered(
            lambda item: item.state == 'active')[:1] or account.success_profile_ids[:1]
        milestones = profile.milestone_ids if profile else self.env['cs.success.milestone']
        milestone_lines = [
            '- [%s] %s | target=%s | evidence=%s | blocker=%s' % (
                milestone.state, milestone.name, milestone.target_date,
                milestone.evidence or '-', milestone.blocker or '-')
            for milestone in milestones.sorted(lambda item: (item.target_date, item.sequence))
        ]
        wallets = self.env['cs.support.wallet'].sudo().search([
            ('cs_account_id', '=', account.id),
        ])
        wallet_lines = [
            '- %s: purchased %.1f h, used %.1f h, remaining %.1f h, status %s, expiry %s' % (
                wallet.product_id.display_name, wallet.purchased_hours, wallet.used_hours,
                wallet.remaining_hours, wallet.status, wallet.expiry_date)
            for wallet in wallets
        ]
        return {
            'snapshot_captured_on': fields.Datetime.now(),
            'health_score_snapshot': account.health_score,
            'csat_snapshot': account.csat_latest,
            'nps_snapshot': account.nps_latest,
            'sentiment_snapshot': account.sentiment_score,
            'open_tickets_snapshot': account.open_tickets_count,
            'sla_failed_snapshot': account.sla_failed_count,
            'support_purchased_snapshot': account.support_hours_purchased,
            'support_used_snapshot': account.support_hours_used,
            'support_remaining_snapshot': account.support_hours_remaining,
            'objectives_snapshot': profile.business_objectives if profile else False,
            'success_criteria_snapshot': profile.success_criteria if profile else False,
            'milestones_snapshot': '\n'.join(milestone_lines) or _('No success milestones recorded.'),
            'support_snapshot': '\n'.join(wallet_lines) or _('No support-hour packages recorded.'),
        }

    def action_prepare(self):
        for review in self:
            if review.state != 'draft':
                continue
            values = {} if review.snapshot_captured_on else review._snapshot_values()
            values.update({
                'state': 'prepared',
                'prepared_on': fields.Datetime.now(),
                'prepared_by_id': self.env.user.id,
            })
            if not review.agenda:
                values['agenda'] = _(
                    '1. Reconfirm customer objectives and priorities\n'
                    '2. Review achieved value and supporting evidence\n'
                    '3. Review success milestones, adoption and support\n'
                    '4. Discuss risks and blockers\n'
                    '5. Agree commitments, owners and next review')
            review.sudo().write(values)
        return True

    def action_mark_held(self):
        for review in self:
            if review.state != 'prepared':
                raise UserError(_('Prepare the value review before marking it as held.'))
            review.sudo().write({
                'state': 'held', 'held_on': fields.Datetime.now()})
        return True

    def action_close(self):
        for review in self:
            if review.state != 'held':
                raise UserError(_('Mark the value review as held before closing it.'))
            if not review.value_realized or not review.commitments or not review.next_review_date:
                raise UserError(_(
                    'Record the customer-confirmed value, agreed commitments, and next review date before closing.'))
            if bool(review.next_step) != bool(review.next_step_date):
                raise UserError(_('Set both the next step and its date, or leave both empty.'))
            review.sudo().write({
                'state': 'closed', 'closed_on': fields.Datetime.now()})
            profile = review.cs_account_id.success_profile_ids.filtered(
                lambda item: item.state == 'active')[:1]
            if profile:
                profile.write({
                    'last_reviewed_on': review.review_date,
                    'review_date': review.next_review_date,
                })
            if review.next_step:
                account = review.cs_account_id
                existing = account.activity_ids.filtered(
                    lambda activity: activity.user_id == account.csm_user_id
                    and activity.date_deadline == review.next_step_date
                    and activity.summary == review.next_step)
                if not existing:
                    account.activity_schedule(
                        'mail.mail_activity_data_todo',
                        user_id=account.csm_user_id.id,
                        date_deadline=review.next_step_date,
                        summary=review.next_step,
                        note=review.commitments,
                    )
            review._refresh_daily_work_item()
        return True

    def action_cancel(self):
        reviews = self.filtered(lambda review: review.state != 'closed')
        reviews.sudo().write({'state': 'cancelled'})
        reviews._refresh_daily_work_item()

    def action_set_draft(self):
        self.filtered(lambda review: review.state == 'cancelled').sudo().write({'state': 'draft'})

    def _ai_context(self):
        self.ensure_one()
        return (
            '=== FROZEN REVIEW SNAPSHOT ===\n'
            'Customer: %s\nPeriod: %s to %s\nObjectives: %s\nSuccess criteria: %s\n'
            'Milestones:\n%s\nSupport:\n%s\n'
            'Health=%s; CSAT=%s; Survey=%s; Sentiment=%s; Open tickets=%s; Failed SLA=%s' % (
                self.partner_id.name, self.period_start, self.period_end,
                self.objectives_snapshot or '-', self.success_criteria_snapshot or '-',
                self.milestones_snapshot or '-', self.support_snapshot or '-',
                self.health_score_snapshot, self.csat_snapshot, self.nps_snapshot,
                self.sentiment_snapshot, self.open_tickets_snapshot, self.sla_failed_snapshot,
            )
        )

    def action_generate_ai_draft(self):
        self.ensure_one()
        if self.state != 'prepared':
            raise UserError(_('Prepare and freeze the review data before generating the AI draft.'))
        if not self.company_id.cs_ai_value_review_enabled:
            raise UserError(_('Enable AI Value Review Drafts in Customer Success settings first.'))
        agent = self.env.ref(
            'era_customer_success.cs_value_review_agent', raise_if_not_found=False)
        if not agent:
            raise UserError(_('The AI value-review agent is not available.'))
        try:
            response = agent.with_user(self.env.ref('base.user_root')).get_direct_response(
                prompt=self._ai_context())
            data = _cs_extract_json(response[0] if response else '')
        except Exception as error:
            _logger.warning('Value review AI draft failed for review %s: %s', self.id, error)
            raise UserError(_('AI value-review generation failed. Check the AI provider configuration.'))
        if not isinstance(data, dict):
            raise UserError(_('The AI returned an invalid value-review draft.'))
        vals = {'ai_generated_on': fields.Datetime.now()}
        for field_name in (
                'agenda', 'data_observations', 'discussion_questions',
                'risks_and_blockers', 'potential_needs'):
            value = data.get(field_name)
            if value and not self[field_name]:
                vals[field_name] = str(value)[:8000]
        self.write(vals)
        return True

    @api.model
    def _cron_prepare_upcoming_reviews(self):
        today = fields.Date.context_today(self)
        profiles = self.env['cs.success.profile'].sudo().search([
            ('state', '=', 'active'),
            ('review_date', '!=', False),
            ('review_date', '<=', today + timedelta(days=14)),
        ])
        for profile in profiles:
            existing = self.sudo().search([
                ('cs_account_id', '=', profile.cs_account_id.id),
                ('review_date', '=', profile.review_date),
            ], limit=1)
            if existing:
                continue
            try:
                with self.env.cr.savepoint():
                    review = self.sudo().create({
                        'cs_account_id': profile.cs_account_id.id,
                        'review_date': profile.review_date,
                        'period_end': profile.review_date,
                        'period_start': profile.review_date - timedelta(days=90),
                        'next_review_date': profile.review_date + timedelta(days=90),
                        'stakeholder_ids': [(6, 0, profile.stakeholder_ids.ids)],
                    })
            except IntegrityError:
                continue
            account = profile.cs_account_id
            summary = _('Prepare value review: %s', account.partner_id.name)
            existing_activity = account.activity_ids.filtered(
                lambda activity: activity.user_id == account.csm_user_id
                and activity.date_deadline == profile.review_date
                and activity.summary == summary)
            if not existing_activity:
                account.activity_schedule(
                    'mail.mail_activity_data_todo',
                    user_id=account.csm_user_id.id,
                    date_deadline=profile.review_date,
                    summary=summary,
                    note=_('Review customer value, evidence, risks, and commitments.'),
                )
            review.message_post(body=_('Value review created automatically from the active success plan.'))
            profile._refresh_daily_work_item()
        return True

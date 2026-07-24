# -*- coding: utf-8 -*-
from psycopg2 import IntegrityError

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError


class CsVocInsight(models.Model):
    _name = 'cs.voc.insight'
    _description = 'Voice of Customer Insight'
    _order = 'priority_rank desc, insight_date desc, id desc'
    _check_company_auto = True

    name = fields.Char(required=True)
    cs_account_id = fields.Many2one(
        'cs.account', string='Customer', required=True, ondelete='cascade',
        check_company=True, index=True)
    partner_id = fields.Many2one(
        related='cs_account_id.partner_id', string='Customer Company', store=True)
    company_id = fields.Many2one(
        related='cs_account_id.company_id', store=True, index=True)
    csm_user_id = fields.Many2one(
        related='cs_account_id.csm_user_id', string='CSM Engineer', store=True, index=True)
    insight_date = fields.Date(required=True, default=fields.Date.context_today, index=True)
    source_type = fields.Selection([
        ('manual', 'Manual'),
        ('value_review', 'Value Review'),
        ('adoption_assessment', 'Adoption Assessment'),
    ], required=True, default='manual')
    source_key = fields.Char(readonly=True, copy=False, index=True)
    source_res_id = fields.Integer(readonly=True, copy=False)
    value_review_id = fields.Many2one(
        'cs.value.review', readonly=True, ondelete='set null', copy=False)
    adoption_assessment_id = fields.Many2one(
        'cs.adoption.assessment', readonly=True, ondelete='set null', copy=False)
    theme = fields.Selection([
        ('value', 'Realized Value'),
        ('priority', 'Customer Priority'),
        ('risk', 'Risk or Complaint'),
        ('adoption', 'Adoption'),
        ('support', 'Support Experience'),
        ('need', 'Need to Validate'),
        ('other', 'Other'),
    ], required=True, default='other')
    sentiment = fields.Selection([
        ('positive', 'Positive'),
        ('neutral', 'Neutral'),
        ('negative', 'Negative'),
    ], required=True, default='neutral')
    priority = fields.Selection([
        ('low', 'Low'), ('medium', 'Medium'), ('high', 'High'),
    ], required=True, default='medium', index=True)
    priority_rank = fields.Integer(compute='_compute_priority_rank', store=True)
    summary = fields.Text(required=True)
    evidence = fields.Text()
    customer_priorities = fields.Text()
    risks_or_blockers = fields.Text(string='Risks or Blockers')
    suggested_response = fields.Text(string='Suggested Customer Success Response')
    adoption_score = fields.Float(readonly=True)
    adoption_confidence = fields.Float(readonly=True)
    state = fields.Selection([
        ('new', 'New'),
        ('triaged', 'Triaged'),
        ('acted', 'Action Taken'),
        ('closed', 'Closed'),
        ('dismissed', 'Dismissed'),
    ], default='new', required=True, index=True)
    next_step = fields.Char()
    next_step_date = fields.Date()
    action_note = fields.Text()
    resolution_note = fields.Text()
    triaged_on = fields.Datetime(readonly=True, copy=False)
    triaged_by_id = fields.Many2one('res.users', readonly=True, copy=False)
    acted_on = fields.Datetime(readonly=True, copy=False)
    acted_by_id = fields.Many2one('res.users', readonly=True, copy=False)
    closed_on = fields.Datetime(readonly=True, copy=False)
    closed_by_id = fields.Many2one('res.users', readonly=True, copy=False)

    _source_key_unique = models.Constraint(
        'unique(source_key)',
        'This Voice of Customer source has already been captured.')

    _snapshot_fields = {
        'name', 'cs_account_id', 'insight_date', 'source_type', 'source_key',
        'source_res_id', 'value_review_id', 'adoption_assessment_id',
        'theme', 'sentiment', 'priority', 'summary', 'evidence',
        'customer_priorities', 'risks_or_blockers', 'suggested_response',
        'adoption_score', 'adoption_confidence',
    }
    _audit_fields = {
        'triaged_on', 'triaged_by_id', 'acted_on', 'acted_by_id',
        'closed_on', 'closed_by_id',
    }

    @api.depends('priority')
    def _compute_priority_rank(self):
        ranks = {'low': 1, 'medium': 2, 'high': 3}
        for insight in self:
            insight.priority_rank = ranks.get(insight.priority, 0)

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            for vals in vals_list:
                if vals.get('source_type', 'manual') != 'manual' or vals.get('state', 'new') != 'new':
                    raise UserError(_('Automatic Voice of Customer insights can only be created from their source workflow.'))
                if vals.get('source_key') or vals.get('source_res_id'):
                    raise UserError(_('Manual Voice of Customer insights cannot set automatic source fields.'))
        return super().create(vals_list)

    def write(self, vals):
        if self._snapshot_fields.intersection(vals):
            raise UserError(_('Voice of Customer source snapshots cannot be modified.'))
        if self._audit_fields.intersection(vals) and not self.env.su:
            raise UserError(_('Voice of Customer audit fields are managed by workflow actions.'))
        if 'state' in vals and not self.env.su:
            raise UserError(_('Use the Voice of Customer workflow buttons to change its status.'))
        return super().write(vals)

    @api.constrains('value_review_id', 'adoption_assessment_id', 'cs_account_id')
    def _check_source_account(self):
        for insight in self:
            if insight.value_review_id and insight.value_review_id.cs_account_id != insight.cs_account_id:
                raise ValidationError(_('The value review must belong to the Voice of Customer account.'))
            if (insight.adoption_assessment_id
                    and insight.adoption_assessment_id.cs_account_id != insight.cs_account_id):
                raise ValidationError(_('The adoption assessment must belong to the Voice of Customer account.'))

    @api.model
    def _create_source_snapshot(self, values):
        key = values['source_key']
        existing = self.sudo().search([('source_key', '=', key)], limit=1)
        if existing:
            return existing
        try:
            with self.env.cr.savepoint():
                return self.sudo().create(values)
        except IntegrityError:
            return self.sudo().search([('source_key', '=', key)], limit=1)

    @api.model
    def _capture_value_review(self, review):
        review.ensure_one()
        if review.state != 'closed':
            return self.browse()
        risks = review.risks_and_blockers or ''
        needs = review.potential_needs or ''
        if risks:
            theme, sentiment, priority = 'risk', 'negative', 'high'
        elif needs:
            theme, sentiment, priority = 'need', 'neutral', 'medium'
        else:
            theme = 'value' if review.value_realized else 'priority'
            sentiment = 'positive' if review.value_realized else 'neutral'
            priority = 'low' if review.value_realized else 'medium'
        summary_parts = [
            review.value_realized,
            review.customer_priorities,
            risks,
            needs,
        ]
        return self._create_source_snapshot({
            'name': _('Value Review Voice - %s', review.partner_id.name),
            'cs_account_id': review.cs_account_id.id,
            'insight_date': review.review_date,
            'source_type': 'value_review',
            'source_key': 'value-review:%s' % review.id,
            'source_res_id': review.id,
            'value_review_id': review.id,
            'theme': theme,
            'sentiment': sentiment,
            'priority': priority,
            'summary': '\n\n'.join(part for part in summary_parts if part) or review.commitments,
            'evidence': review.evidence,
            'customer_priorities': review.customer_priorities,
            'risks_or_blockers': risks,
            'suggested_response': review.commitments,
        })

    @api.model
    def _capture_adoption_assessment(self, assessment):
        assessment.ensure_one()
        if assessment.state != 'confirmed':
            return self.browse()
        reliable_low = assessment.status == 'low' and assessment.confidence >= 50
        if reliable_low:
            sentiment, priority = 'negative', 'high'
        elif assessment.status in ('low', 'watch') or assessment.blockers:
            sentiment, priority = 'neutral', 'medium'
        else:
            sentiment, priority = 'positive', 'low'
        return self._create_source_snapshot({
            'name': _('Adoption Voice - %s', assessment.partner_id.name),
            'cs_account_id': assessment.cs_account_id.id,
            'insight_date': assessment.assessment_date,
            'source_type': 'adoption_assessment',
            'source_key': 'adoption-assessment:%s' % assessment.id,
            'source_res_id': assessment.id,
            'adoption_assessment_id': assessment.id,
            'theme': 'adoption',
            'sentiment': sentiment,
            'priority': priority,
            'summary': assessment.blockers or assessment.evidence,
            'evidence': assessment.evidence,
            'risks_or_blockers': assessment.blockers,
            'suggested_response': assessment.enablement_plan,
            'adoption_score': assessment.score,
            'adoption_confidence': assessment.confidence,
        })

    def action_triage(self):
        self.check_access('write')
        self.filtered(lambda item: item.state == 'new').sudo().write({
            'state': 'triaged',
            'triaged_on': fields.Datetime.now(),
            'triaged_by_id': self.env.user.id,
        })

    def action_mark_acted(self, note=None):
        self.check_access('write')
        values = {
            'state': 'acted',
            'acted_on': fields.Datetime.now(),
            'acted_by_id': self.env.user.id,
        }
        if note:
            values['action_note'] = note
        self.filtered(lambda item: item.state in ('new', 'triaged')).sudo().write(values)

    def action_close(self):
        self.check_access('write')
        for insight in self:
            if insight.state not in ('acted', 'triaged') or not insight.resolution_note:
                raise UserError(_('Record the response and resolution before closing the customer insight.'))
        self.sudo().write({
            'state': 'closed',
            'closed_on': fields.Datetime.now(),
            'closed_by_id': self.env.user.id,
        })

    def action_dismiss(self, note=None):
        self.check_access('write')
        values = {
            'state': 'dismissed',
            'closed_on': fields.Datetime.now(),
            'closed_by_id': self.env.user.id,
            'resolution_note': note or _('Dismissed as not relevant.'),
        }
        self.filtered(lambda item: item.state in ('new', 'triaged')).sudo().write(values)

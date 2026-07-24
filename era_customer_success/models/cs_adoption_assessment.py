# -*- coding: utf-8 -*-
import json
import logging
from datetime import timedelta

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError

from .cs_account import _cs_extract_json

_logger = logging.getLogger(__name__)


class CsAdoptionAssessment(models.Model):
    _name = 'cs.adoption.assessment'
    _description = 'Customer Adoption Assessment'
    _order = 'assessment_date desc, id desc'
    _check_company_auto = True

    cs_account_id = fields.Many2one(
        'cs.account', string='Customer', required=True, ondelete='cascade',
        check_company=True, index=True)
    partner_id = fields.Many2one(
        related='cs_account_id.partner_id', string='Customer Company', store=True)
    company_id = fields.Many2one(
        related='cs_account_id.company_id', store=True, index=True)
    csm_user_id = fields.Many2one(
        related='cs_account_id.csm_user_id', string='CSM Engineer', store=True, index=True)
    state = fields.Selection([
        ('draft', 'Draft'), ('confirmed', 'Confirmed'), ('cancelled', 'Cancelled'),
    ], default='draft', required=True, index=True)
    assessment_date = fields.Date(
        required=True, default=fields.Date.context_today, index=True)
    next_assessment_date = fields.Date(string='Next Adoption Review')
    source = fields.Selection([
        ('csm_check', 'CSM Check'),
        ('customer_review', 'Customer Review'),
        ('import', 'Imported Aggregate'),
        ('telemetry', 'Verified Telemetry'),
    ], default='csm_check', required=True)
    source_reference = fields.Char(
        help='Report, meeting, integration, or other evidence supporting these aggregates.')

    licensed_users = fields.Integer(string='Licensed Users')
    active_users_30d = fields.Integer(string='Active Users (30 Days)')
    key_workflows_total = fields.Integer(string='Key Workflows')
    adopted_workflows = fields.Integer(string='Adopted Workflows')
    onboarding_measured = fields.Boolean(string='Onboarding Measured')
    onboarding_percent = fields.Float(string='Onboarding Completion (%)')
    usage_frequency = fields.Selection([
        ('unknown', 'Unknown'),
        ('daily', 'Daily'),
        ('weekly', 'Weekly'),
        ('monthly', 'Monthly'),
        ('rare', 'Rarely'),
    ], default='unknown', required=True)
    blockers = fields.Text()
    evidence = fields.Text()
    enablement_plan = fields.Text(string='Enablement Plan')

    score = fields.Float(string='Adoption Score', compute='_compute_score', store=True)
    confidence = fields.Float(string='Data Confidence', compute='_compute_score', store=True)
    status = fields.Selection([
        ('unknown', 'Unknown'),
        ('healthy', 'Healthy'),
        ('watch', 'Needs Attention'),
        ('low', 'Low Adoption'),
    ], compute='_compute_score', store=True)
    confirmed_on = fields.Datetime(readonly=True, copy=False)
    confirmed_by_id = fields.Many2one('res.users', readonly=True, copy=False)
    ai_generated_on = fields.Datetime(readonly=True, copy=False)
    ai_adoption_enabled = fields.Boolean(
        related='company_id.cs_ai_adoption_enabled', readonly=True)

    _account_date_unique = models.Constraint(
        'unique(cs_account_id, assessment_date)',
        'Only one adoption assessment per customer and date is allowed.')

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            protected_create = {'confirmed_on', 'confirmed_by_id', 'ai_generated_on'}
            for vals in vals_list:
                if vals.get('state', 'draft') != 'draft' or protected_create.intersection(vals):
                    raise UserError(_(
                        'Create adoption assessments as drafts and confirm them through the workflow.'))
        return super().create(vals_list)

    @api.depends(
        'licensed_users', 'active_users_30d', 'key_workflows_total',
        'adopted_workflows', 'onboarding_measured', 'onboarding_percent',
        'usage_frequency')
    def _compute_score(self):
        frequency_scores = {'daily': 100.0, 'weekly': 70.0, 'monthly': 40.0, 'rare': 10.0}
        for assessment in self:
            components = []
            if assessment.licensed_users > 0:
                components.append(min(100.0, 100.0 * assessment.active_users_30d / assessment.licensed_users))
            if assessment.key_workflows_total > 0:
                components.append(min(100.0, 100.0 * assessment.adopted_workflows / assessment.key_workflows_total))
            if assessment.onboarding_measured:
                components.append(max(0.0, min(100.0, assessment.onboarding_percent)))
            if assessment.usage_frequency in frequency_scores:
                components.append(frequency_scores[assessment.usage_frequency])
            assessment.confidence = 25.0 * len(components)
            assessment.score = sum(components) / len(components) if components else 0.0
            if not components:
                assessment.status = 'unknown'
            elif assessment.score >= 70:
                assessment.status = 'healthy'
            elif assessment.score >= 40:
                assessment.status = 'watch'
            else:
                assessment.status = 'low'

    @api.constrains(
        'licensed_users', 'active_users_30d', 'key_workflows_total',
        'adopted_workflows', 'onboarding_percent', 'assessment_date')
    def _check_metrics(self):
        for assessment in self:
            if assessment.assessment_date > fields.Date.context_today(assessment):
                raise ValidationError(_('The adoption assessment date cannot be in the future.'))
            if min(
                    assessment.licensed_users, assessment.active_users_30d,
                    assessment.key_workflows_total, assessment.adopted_workflows) < 0:
                raise ValidationError(_('Adoption metrics cannot be negative.'))
            if assessment.licensed_users and assessment.active_users_30d > assessment.licensed_users:
                raise ValidationError(_('Active users cannot exceed licensed users.'))
            if assessment.key_workflows_total and assessment.adopted_workflows > assessment.key_workflows_total:
                raise ValidationError(_('Adopted workflows cannot exceed key workflows.'))
            if assessment.onboarding_measured and not 0 <= assessment.onboarding_percent <= 100:
                raise ValidationError(_('Onboarding completion must be between 0 and 100.'))

    def write(self, vals):
        protected = {
            'cs_account_id', 'assessment_date', 'source', 'source_reference',
            'licensed_users', 'active_users_30d', 'key_workflows_total',
            'adopted_workflows', 'onboarding_measured', 'onboarding_percent',
            'usage_frequency', 'blockers', 'evidence',
            'enablement_plan',
            'next_assessment_date', 'confirmed_on', 'confirmed_by_id',
            'ai_generated_on',
        }
        if self.filtered(lambda item: item.state == 'confirmed') and protected.intersection(vals):
            raise UserError(_('Confirmed adoption evidence cannot be modified.'))
        if 'state' in vals and not self.env.su:
            raise UserError(_('Use the adoption assessment workflow buttons to change its status.'))
        return super().write(vals)

    def action_confirm(self):
        self.check_access('write')
        for assessment in self:
            if assessment.state != 'draft':
                continue
            if assessment.confidence <= 0:
                raise UserError(_('Record at least one measured adoption component before confirming.'))
            if not assessment.evidence or not assessment.source_reference:
                raise UserError(_('Record the evidence and its source before confirming adoption.'))
            values = {
                'state': 'confirmed',
                'confirmed_on': fields.Datetime.now(),
                'confirmed_by_id': self.env.user.id,
            }
            if not assessment.next_assessment_date:
                values['next_assessment_date'] = assessment.assessment_date + timedelta(days=90)
            assessment.sudo().write(values)
            self.env['cs.voc.insight']._capture_adoption_assessment(assessment)
            assessment.cs_account_id._refresh_adoption_work_item()

    def action_cancel(self):
        self.check_access('write')
        assessments = self.filtered(lambda item: item.state != 'confirmed')
        assessments.sudo().write({'state': 'cancelled'})

    def action_set_draft(self):
        self.check_access('write')
        self.filtered(lambda item: item.state == 'cancelled').sudo().write({'state': 'draft'})

    def action_generate_ai_plan(self):
        self.ensure_one()
        self.check_access('write')
        if self.state != 'draft':
            raise UserError(_('AI enablement planning is available only on draft assessments.'))
        if not self.company_id.cs_ai_adoption_enabled:
            raise UserError(_('Enable AI Adoption Plans in Customer Success settings first.'))
        agent = self.env.ref('era_customer_success.cs_adoption_agent', raise_if_not_found=False)
        if not agent:
            raise UserError(_('The AI adoption agent is not available.'))
        payload = {
            'licensed_users': self.licensed_users,
            'active_users_30d': self.active_users_30d,
            'key_workflows_total': self.key_workflows_total,
            'adopted_workflows': self.adopted_workflows,
            'onboarding_measured': self.onboarding_measured,
            'onboarding_percent': self.onboarding_percent,
            'usage_frequency': self.usage_frequency,
            'score': self.score,
            'confidence': self.confidence,
            'blockers_to_address': (self.blockers or '')[:2000],
        }
        try:
            response = agent.with_user(self.env.ref('base.user_root')).get_direct_response(
                prompt=json.dumps(payload, ensure_ascii=False))
            data = _cs_extract_json(response[0] if response else '')
        except Exception as error:
            _logger.warning('Adoption AI plan failed for assessment %s: %s', self.id, error)
            raise UserError(_('AI adoption planning failed. Check the AI provider configuration.'))
        if not isinstance(data, dict) or not data.get('enablement_plan'):
            raise UserError(_('The AI returned an invalid adoption plan.'))
        if not self.enablement_plan:
            self.enablement_plan = str(data['enablement_plan'])[:8000]
        self.ai_generated_on = fields.Datetime.now()
        return True

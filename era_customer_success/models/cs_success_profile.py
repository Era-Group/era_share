# -*- coding: utf-8 -*-
import logging
from datetime import timedelta

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError

from .cs_account import _cs_extract_json

_logger = logging.getLogger(__name__)


class CsSuccessProfile(models.Model):
    _name = 'cs.success.profile'
    _description = 'Customer Success Plan'
    _order = 'review_date, id'
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
        ('draft', 'Draft'),
        ('active', 'Active'),
        ('archived', 'Archived'),
    ], default='draft', required=True, index=True)
    business_objectives = fields.Text(string='Customer Business Objectives')
    desired_outcomes = fields.Text(string='Desired Outcomes')
    success_criteria = fields.Text(string='How We Measure Success')
    value_hypothesis = fields.Text(string='ERA Value Plan')
    challenges = fields.Text(string='Current Challenges')
    review_date = fields.Date(string='Next Plan Review', index=True)
    last_reviewed_on = fields.Date(string='Last Reviewed On', readonly=True)
    ai_generated_on = fields.Datetime(string='AI Draft Generated On', readonly=True)
    ai_success_plan_enabled = fields.Boolean(
        related='company_id.cs_ai_success_plan_enabled', readonly=True)
    stakeholder_ids = fields.One2many(
        'cs.success.stakeholder', 'profile_id', string='Stakeholders')
    milestone_ids = fields.One2many(
        'cs.success.milestone', 'profile_id', string='Success Milestones')

    _account_unique = models.Constraint(
        'unique(cs_account_id)',
        'A success plan already exists for this customer.')

    def action_activate(self):
        self.write({
            'state': 'active',
            'last_reviewed_on': fields.Date.context_today(self),
        })

    def action_archive(self):
        self.write({'state': 'archived'})

    def action_set_draft(self):
        self.write({'state': 'draft'})

    def action_reopen(self):
        self.write({'state': 'draft'})

    @api.model_create_multi
    def create(self, vals_list):
        profiles = super().create(vals_list)
        profiles.filtered(lambda profile: profile.state == 'active')._refresh_daily_work_item()
        return profiles

    def write(self, vals):
        if vals.get('state') == 'active' and 'last_reviewed_on' not in vals:
            vals['last_reviewed_on'] = fields.Date.context_today(self)
        state_changed = 'state' in vals
        result = super().write(vals)
        if state_changed:
            self._refresh_daily_work_item()
        return result

    def _refresh_daily_work_item(self):
        """Synchronize this account's current work item after plan changes."""
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
            elif current and current.action_type == 'success_milestone':
                current.write({
                    'state': 'dismissed',
                    'outcome': 'not_relevant',
                    'outcome_note': _('The success plan no longer requires this work item.'),
                    'completed_on': fields.Datetime.now(),
                    'completed_by_id': self.env.user.id,
                })

    def _ai_context(self):
        self.ensure_one()
        account = self.cs_account_id
        contacts = self.env['res.partner'].sudo().browse(account._partner_ids()).filtered(
            lambda partner: partner.id != account.partner_id.id and partner.name)
        contact_lines = [
            '- %s | title=%s' % (contact.name, contact.function or '-')
            for contact in contacts[:20]
        ]
        return (
            '%s\n\n=== CURRENT SUCCESS PLAN ===\nObjectives: %s\nChallenges: %s\n'
            'Desired outcomes: %s\nSuccess criteria: %s\nERA value plan: %s\n\n'
            '=== KPI TREND ===\n%s\n\n=== KNOWN CONTACTS ===\n%s' % (
                account._build_situation_summary(),
                self.business_objectives or '-', self.challenges or '-',
                self.desired_outcomes or '-', self.success_criteria or '-',
                self.value_hypothesis or '-',
                account._build_snapshot_trend(),
                '\n'.join(contact_lines) or 'No named contacts available.',
            )
        )

    def action_generate_ai_draft(self):
        """Fill missing plan fields and append reviewable, non-duplicate draft lines."""
        self.ensure_one()
        if self.state != 'draft':
            raise UserError(_('Move the success plan to Draft before generating an AI revision.'))
        if not self.company_id.cs_ai_success_plan_enabled:
            raise UserError(_('Enable AI Success Plan Drafts in Customer Success settings first.'))
        agent = self.env.ref(
            'era_customer_success.cs_success_plan_agent', raise_if_not_found=False)
        if not agent:
            raise UserError(_('The AI success-plan agent is not available.'))
        try:
            response = agent.with_user(self.env.ref('base.user_root')).get_direct_response(
                prompt=self._ai_context())
            data = _cs_extract_json(response[0] if response else '')
        except Exception as error:
            _logger.warning('Success plan AI draft failed for profile %s: %s', self.id, error)
            raise UserError(_(
                'AI success-plan generation failed. Check the AI provider configuration.'))
        if not isinstance(data, dict):
            raise UserError(_('The AI returned an invalid success-plan draft.'))

        vals = {'ai_generated_on': fields.Datetime.now()}
        for field_name in (
                'business_objectives', 'desired_outcomes', 'success_criteria',
                'value_hypothesis', 'challenges'):
            value = data.get(field_name)
            if value and not self[field_name]:
                vals[field_name] = str(value)[:5000]
        if not self.review_date:
            vals['review_date'] = fields.Date.context_today(self) + timedelta(days=90)
        self.write(vals)

        valid_roles = dict(self.env['cs.success.stakeholder']._fields['role'].selection)
        contacts = self.env['res.partner'].sudo().browse(
            self.cs_account_id._partner_ids()).filtered('name')
        contacts_by_name = {contact.name.strip().casefold(): contact for contact in contacts}
        existing_contacts = set(self.stakeholder_ids.mapped('partner_id').ids)
        existing_names = {
            name.strip().casefold() for name in self.stakeholder_ids.mapped('name') if name
        }
        for item in (data.get('stakeholders') or [])[:10]:
            if not isinstance(item, dict):
                continue
            name = str(item.get('name') or '').strip()
            contact = contacts_by_name.get(name.casefold())
            if not contact or contact.id in existing_contacts or name.casefold() in existing_names:
                continue
            role = item.get('role') if item.get('role') in valid_roles else 'other'
            self.env['cs.success.stakeholder'].create({
                'profile_id': self.id,
                'partner_id': contact.id,
                'role': role,
                'influence': item.get('influence') if item.get('influence') in ('low', 'medium', 'high') else 'medium',
            })
            existing_contacts.add(contact.id)

        existing_milestones = {
            name.strip().casefold() for name in self.milestone_ids.mapped('name') if name
        }
        today = fields.Date.context_today(self)
        for sequence, item in enumerate((data.get('milestones') or [])[:5], start=10):
            if not isinstance(item, dict):
                continue
            name = str(item.get('name') or '').strip()[:200]
            if not name or name.casefold() in existing_milestones:
                continue
            try:
                target_in_days = max(1, min(int(item.get('target_in_days') or 30), 180))
            except (TypeError, ValueError):
                target_in_days = 30
            priority = item.get('priority')
            if priority not in ('low', 'medium', 'high', 'urgent'):
                priority = 'medium'
            self.env['cs.success.milestone'].create({
                'profile_id': self.id,
                'sequence': sequence,
                'name': name,
                'description': str(item.get('description') or '')[:2000],
                'success_criterion': str(item.get('success_criterion') or '')[:2000],
                'target_date': today + timedelta(days=target_in_days),
                'priority': priority,
            })
            existing_milestones.add(name.casefold())
        return True


class CsSuccessStakeholder(models.Model):
    _name = 'cs.success.stakeholder'
    _description = 'Customer Success Stakeholder'
    _order = 'is_primary desc, influence desc, id'
    _check_company_auto = True

    profile_id = fields.Many2one(
        'cs.success.profile', required=True, ondelete='cascade', check_company=True)
    cs_account_id = fields.Many2one(related='profile_id.cs_account_id', store=True, index=True)
    company_id = fields.Many2one(related='profile_id.company_id', store=True, index=True)
    csm_user_id = fields.Many2one(related='profile_id.csm_user_id', store=True, index=True)
    partner_id = fields.Many2one(
        'res.partner', string='Contact', required=True, ondelete='restrict')
    name = fields.Char(related='partner_id.name', store=True, readonly=True)
    role = fields.Selection([
        ('champion', 'Champion'),
        ('executive_sponsor', 'Executive Sponsor'),
        ('decision_maker', 'Decision Maker'),
        ('economic_buyer', 'Economic Buyer'),
        ('admin', 'System Administrator'),
        ('end_user', 'Key User'),
        ('influencer', 'Influencer'),
        ('other', 'Other'),
    ], default='other', required=True)
    influence = fields.Selection([
        ('low', 'Low'), ('medium', 'Medium'), ('high', 'High'),
    ], default='medium', required=True)
    relationship_status = fields.Selection([
        ('strong', 'Strong'), ('neutral', 'Neutral'), ('weak', 'Weak'),
        ('unknown', 'Unknown'),
    ], default='unknown', required=True)
    is_primary = fields.Boolean(string='Primary Contact')
    last_contact_date = fields.Date(string='Last Contact')
    notes = fields.Text()

    @api.constrains('partner_id', 'cs_account_id')
    def _check_contact_customer(self):
        for stakeholder in self.filtered('partner_id'):
            if stakeholder.partner_id.id not in stakeholder.cs_account_id._partner_ids():
                raise ValidationError(_(
                    'The stakeholder contact must belong to the customer company.'))


class CsSuccessMilestone(models.Model):
    _name = 'cs.success.milestone'
    _description = 'Customer Success Milestone'
    _order = 'target_date, sequence, id'
    _check_company_auto = True

    profile_id = fields.Many2one(
        'cs.success.profile', required=True, ondelete='cascade', check_company=True)
    cs_account_id = fields.Many2one(related='profile_id.cs_account_id', store=True, index=True)
    company_id = fields.Many2one(related='profile_id.company_id', store=True, index=True)
    csm_user_id = fields.Many2one(related='profile_id.csm_user_id', store=True, index=True)
    sequence = fields.Integer(default=10)
    name = fields.Char(required=True)
    description = fields.Text()
    success_criterion = fields.Text(string='Evidence of Success')
    target_date = fields.Date(required=True, index=True)
    owner_user_id = fields.Many2one(
        related='profile_id.csm_user_id', string='Owner', store=True, readonly=True)
    priority = fields.Selection([
        ('low', 'Low'), ('medium', 'Medium'), ('high', 'High'), ('urgent', 'Urgent'),
    ], default='medium', required=True)
    state = fields.Selection([
        ('planned', 'Planned'),
        ('in_progress', 'In Progress'),
        ('achieved', 'Achieved'),
        ('blocked', 'Blocked'),
        ('cancelled', 'Cancelled'),
    ], default='planned', required=True, index=True)
    achieved_date = fields.Date(readonly=True)
    evidence = fields.Text()
    blocker = fields.Text()
    is_overdue = fields.Boolean(compute='_compute_is_overdue')
    attention_rank = fields.Integer(compute='_compute_attention_rank', store=True)

    @api.depends('state', 'target_date')
    def _compute_is_overdue(self):
        today = fields.Date.context_today(self)
        for milestone in self:
            milestone.is_overdue = (
                milestone.state not in ('achieved', 'cancelled')
                and milestone.target_date < today)

    @api.depends('state', 'priority')
    def _compute_attention_rank(self):
        priority_rank = {'low': 1, 'medium': 2, 'high': 3, 'urgent': 4}
        for milestone in self:
            milestone.attention_rank = (
                100 if milestone.state == 'blocked' else 0
            ) + priority_rank.get(milestone.priority, 0)

    def write(self, vals):
        if vals.get('state') == 'achieved' and 'achieved_date' not in vals:
            vals['achieved_date'] = fields.Date.context_today(self)
        elif vals.get('state') and vals.get('state') != 'achieved':
            vals['achieved_date'] = False
        result = super().write(vals)
        if {'state', 'target_date', 'priority'} & set(vals):
            self.mapped('profile_id').filtered(
                lambda profile: profile.state == 'active')._refresh_daily_work_item()
        return result

    @api.model_create_multi
    def create(self, vals_list):
        milestones = super().create(vals_list)
        milestones.mapped('profile_id').filtered(
            lambda profile: profile.state == 'active')._refresh_daily_work_item()
        return milestones

    def unlink(self):
        profiles = self.mapped('profile_id')
        result = super().unlink()
        profiles.filtered(lambda profile: profile.state == 'active')._refresh_daily_work_item()
        return result

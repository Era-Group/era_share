# -*- coding: utf-8 -*-
from datetime import timedelta

from psycopg2 import IntegrityError

from odoo import fields, models, _


class CsServiceRecommendationWizard(models.TransientModel):
    _name = 'cs.service.recommendation.wizard'
    _description = 'Review Customer Service Recommendations'

    cs_account_id = fields.Many2one('cs.account', required=True, readonly=True)
    partner_id = fields.Many2one(related='cs_account_id.partner_id', readonly=True)
    line_ids = fields.One2many(
        'cs.service.recommendation.line', 'wizard_id', string='Recommendations')

    def action_compute(self):
        self.ensure_one()
        account = self.cs_account_id
        account.check_access('read')
        self.line_ids.unlink()

        adoption = self.env['cs.adoption.assessment'].sudo().search([
            ('cs_account_id', '=', account.id),
            ('state', '=', 'confirmed'),
        ], order='assessment_date desc, id desc', limit=1)
        adoption_match = bool(
            adoption and adoption.confidence >= 50
            and adoption.status in ('watch', 'low'))

        pressure_wallets = self.env['cs.support.wallet'].sudo().search([
            ('cs_account_id', '=', account.id),
            ('status', 'in', ('low', 'critical', 'exhausted', 'expired')),
            ('attention_rank', '>', 0),
        ])

        partner_ids = account._partner_ids()
        recent_tickets = self.env['helpdesk.ticket'].sudo().search([
            ('partner_id', 'in', partner_ids),
            ('stage_id.fold', '=', False),
            ('create_date', '>=', fields.Datetime.now() - timedelta(days=30)),
        ])
        recent_tag_ids = set(recent_tickets.tag_ids.ids)

        sold_lines = self.env['sale.order.line'].sudo().search([
            ('order_id.state', 'in', ('sale', 'done')),
            ('order_partner_id.commercial_partner_id', '=', account.partner_id.id),
            ('display_type', '=', False),
        ])
        purchased_template_ids = set(sold_lines.product_id.product_tmpl_id.ids)

        profile = account.success_profile_ids.filtered(
            lambda item: item.state == 'active')[:1]
        explicit_service_ids = set(profile.recommended_service_ids.ids) if profile else set()
        offerings = self.env['csm.offering'].search([
            ('cs_account_id', '=', account.id),
            ('service_id', '!=', False),
        ])
        open_service_ids = set(offerings.filtered(
            lambda item: item.state in ('draft', 'presented')).service_id.ids)

        recommendations = []
        today = fields.Date.context_today(self)
        for service in self.env['cs.service'].search([('active', '=', True)]):
            if service.id in open_service_ids:
                continue
            if purchased_template_ids.intersection(service.product_tmpl_ids.ids):
                continue
            rejected = offerings.filtered(
                lambda item: item.service_id == service and item.state == 'rejected'
                and item.offering_date
                and item.offering_date >= today - timedelta(days=service.recommendation_cooldown_days))
            if rejected:
                continue

            score = 0
            reasons = []
            if service.recommend_on_low_adoption and adoption_match:
                score += 40
                reasons.append(_(
                    'Measured adoption is %(status)s (%(score).0f%% score, %(confidence).0f%% confidence).',
                    status=adoption.status, score=adoption.score,
                    confidence=adoption.confidence))
            if service.recommend_on_support_pressure and pressure_wallets:
                wallet = max(pressure_wallets, key=lambda item: item.attention_rank)
                score += 30
                reasons.append(_(
                    'Support package %(package)s is %(status)s with %(remaining).1f hours remaining.',
                    package=wallet.product_id.display_name,
                    status=wallet.status, remaining=wallet.remaining_hours))
            if service.recommend_on_sla_failure and account.sla_failed_count:
                score += 20
                reasons.append(_('%s open support tickets have failed SLA.', account.sla_failed_count))
            matching_tags = service.recommendation_ticket_tag_ids.filtered(
                lambda tag: tag.id in recent_tag_ids)
            if matching_tags:
                score += 30
                reasons.append(_(
                    'Recent open tickets match configured need tags: %s.',
                    ', '.join(matching_tags.mapped('name'))))
            if service.id in explicit_service_ids:
                score += 35
                reasons.append(_('The active success plan explicitly links this service to customer goals.'))
            if score >= 30:
                recommendations.append((score, service, '\n'.join('- %s' % reason for reason in reasons)))

        recommendations.sort(key=lambda item: (-item[0], item[1].name.lower(), item[1].id))
        for score, service, reason in recommendations[:3]:
            self.env['cs.service.recommendation.line'].create({
                'wizard_id': self.id,
                'selected': True,
                'service_id': service.id,
                'score': score,
                'reason': reason,
                'discovery_questions': service.discovery_questions,
                'value_outcomes': service.value_outcomes,
            })
        return True

    def action_create_drafts(self):
        self.ensure_one()
        account = self.cs_account_id
        account.check_access('write')
        Offering = self.env['csm.offering']
        for line in self.line_ids.filtered('selected'):
            service = line.service_id
            key = 'service-recommendation:%s:%s:%s' % (
                account.company_id.id, account.id, service.id)
            existing = Offering.search([
                ('cs_account_id', '=', account.id),
                ('service_id', '=', service.id),
                ('state', 'in', ('draft', 'presented')),
            ], limit=1)
            if existing:
                continue
            values = {
                'name': service.name,
                'cs_account_id': account.id,
                'partner_id': account.partner_id.id,
                'company_id': account.company_id.id,
                'csm_user_id': account.csm_user_id.id,
                'service_id': service.id,
                'need_type': 'module' if service.service_type == 'module' else 'service',
                'product_tmpl_ids': [(6, 0, service.product_tmpl_ids.ids)],
                'notes': '%s\n\n%s' % (
                    line.reason or '',
                    _('Discovery questions:\n%s', service.discovery_questions or '-')),
                'is_service_recommendation': True,
                'recommendation_key': key,
                'recommendation_score': line.score,
                'recommendation_reason': line.reason,
            }
            try:
                with self.env.cr.savepoint():
                    Offering.create(values)
            except IntegrityError:
                continue
        return {
            'type': 'ir.actions.act_window',
            'name': _('Draft Offerings to Validate'),
            'res_model': 'csm.offering',
            'view_mode': 'list,form',
            'domain': [
                ('cs_account_id', '=', account.id),
                ('state', '=', 'draft'),
            ],
        }


class CsServiceRecommendationLine(models.TransientModel):
    _name = 'cs.service.recommendation.line'
    _description = 'Customer Service Recommendation Line'
    _order = 'score desc, id'

    wizard_id = fields.Many2one(
        'cs.service.recommendation.wizard', required=True, ondelete='cascade')
    selected = fields.Boolean(default=True)
    service_id = fields.Many2one('cs.service', required=True, readonly=True)
    score = fields.Integer(readonly=True)
    reason = fields.Text(readonly=True)
    discovery_questions = fields.Text(readonly=True)
    value_outcomes = fields.Text(readonly=True)

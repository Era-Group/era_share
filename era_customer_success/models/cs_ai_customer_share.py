import json
import logging
import secrets

from odoo import api, fields, models, _
from odoo.exceptions import UserError

from .ai_utils import extract_json_object


_logger = logging.getLogger(__name__)

ALLOWED_SHARE_FIELDS = {
    'customer_name',
    'last_contact',
    'contact_result',
    'adoption_percent',
    'customer_voice',
    'relationship_health_percent',
}


class CsAiCustomerShare(models.Model):
    _name = 'cs.ai.customer.share'
    _description = 'AI Customer Sharing Table'
    _order = 'id desc'
    _check_company_auto = True

    name = fields.Char(required=True, default=lambda self: _('Odoo Customer Data Review'))
    company_id = fields.Many2one(
        'res.company', required=True, default=lambda self: self.env.company)
    request_description = fields.Text(
        required=True,
        default=lambda self: _(
            'Share customer name, last contact, contact result, adoption percentage, '
            'voice of customer, and relationship health percentage.'),
        string='Requested Information Description')
    account_ids = fields.Many2many(
        'cs.account', 'cs_ai_customer_share_account_rel', 'share_id', 'account_id',
        string='Included Customers', required=True, check_company=True)
    state = fields.Selection([
        ('draft', 'Draft'),
        ('prepared', 'Prepared for Review'),
        ('approved', 'Approved'),
    ], default='draft', required=True, readonly=True)
    selected_fields = fields.Char(readonly=True, copy=False)
    include_customer_name = fields.Boolean(readonly=True, copy=False)
    include_last_contact = fields.Boolean(readonly=True, copy=False)
    include_contact_result = fields.Boolean(readonly=True, copy=False)
    include_adoption_percent = fields.Boolean(readonly=True, copy=False)
    include_customer_voice = fields.Boolean(readonly=True, copy=False)
    include_relationship_health_percent = fields.Boolean(readonly=True, copy=False)
    line_ids = fields.One2many(
        'cs.ai.customer.share.line', 'share_id', string='AI-Prepared Customer Table',
        copy=False)
    prepared_by_id = fields.Many2one('res.users', readonly=True, copy=False)
    prepared_on = fields.Datetime(readonly=True, copy=False)
    approved_by_id = fields.Many2one('res.users', readonly=True, copy=False)
    approved_on = fields.Datetime(readonly=True, copy=False)
    portal_enabled = fields.Boolean(readonly=True, copy=False)
    portal_user_ids = fields.Many2many(
        'res.users', 'cs_ai_customer_share_portal_user_rel',
        'share_id', 'user_id', string='Authorized Portal Users', copy=False,
        domain="[('share', '=', True)]")
    expires_on = fields.Date(
        required=True, default=lambda self: fields.Date.add(fields.Date.today(), days=30))
    access_token = fields.Char(
        default=lambda self: secrets.token_urlsafe(32), readonly=True, copy=False)
    portal_url = fields.Char(compute='_compute_portal_url')
    published_by_id = fields.Many2one('res.users', readonly=True, copy=False)
    published_on = fields.Datetime(readonly=True, copy=False)
    access_count = fields.Integer(readonly=True, copy=False)
    last_accessed_on = fields.Datetime(readonly=True, copy=False)

    @api.depends('access_token')
    def _compute_portal_url(self):
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url', '')
        for share in self:
            share.portal_url = '%s/era/customer-sharing/%s?access_token=%s' % (
                base_url, share.id, share.access_token) if share.id else False

    @api.model
    def default_get(self, field_names):
        values = super().default_get(field_names)
        if 'account_ids' in field_names and not values.get('account_ids'):
            accounts = self.env['cs.account'].search([
                ('company_id', '=', self.env.company.id),
                ('send_to_portal_share', '=', True),
            ])
            values['account_ids'] = [(6, 0, accounts.ids)]
        return values

    def write(self, vals):
        if {'request_description', 'account_ids'}.intersection(vals):
            vals = dict(vals, state='draft', selected_fields=False,
                        prepared_by_id=False, prepared_on=False,
                        approved_by_id=False, approved_on=False,
                        portal_enabled=False, published_by_id=False,
                        published_on=False,
                        include_customer_name=False,
                        include_last_contact=False,
                        include_contact_result=False,
                        include_adoption_percent=False,
                        include_customer_voice=False,
                        include_relationship_health_percent=False)
        return super().write(vals)

    def _analyse_requested_fields(self):
        self.ensure_one()
        agent = self.env.ref(
            'era_customer_success.cs_share_field_selector_agent',
            raise_if_not_found=False)
        if not agent:
            raise UserError(_('The AI sharing-field selector is not available.'))
        prompt = json.dumps({
            'description': self.request_description,
            'allowed_fields': sorted(ALLOWED_SHARE_FIELDS),
        }, ensure_ascii=False)
        try:
            response = agent.with_user(self.env.ref('base.user_root')).get_direct_response(
                prompt=prompt)
            data = extract_json_object(response[0] if response else '')
        except Exception as error:
            _logger.warning('AI sharing-field selection failed for %s: %s', self.id, error)
            raise UserError(_('AI could not analyse the requested sharing fields.'))
        requested = data.get('selected_fields', []) if isinstance(data, dict) else []
        selected = self._filter_allowed_fields(requested)
        if not selected:
            raise UserError(_('The description did not select any allowed sharing field.'))
        return selected

    @api.model
    def _filter_allowed_fields(self, requested):
        return list(dict.fromkeys(
            field_name for field_name in (requested or [])
            if field_name in ALLOWED_SHARE_FIELDS))

    @api.model
    def _safe_percent(self, value, fallback=0.0):
        try:
            normalized = str(value).strip().rstrip('%')
            return min(100.0, max(0.0, float(normalized)))
        except (TypeError, ValueError):
            return min(100.0, max(0.0, float(fallback or 0.0)))

    def _prepare_account_row(self, account, selected):
        agent = self.env.ref(
            'era_customer_success.cs_share_row_builder_agent',
            raise_if_not_found=False)
        if not agent:
            raise UserError(_('The AI customer-table builder is not available.'))
        prompt = json.dumps({
            'selected_fields': selected,
            'customer_snapshot': account._build_situation_summary(),
        }, ensure_ascii=False)
        try:
            response = agent.with_user(self.env.ref('base.user_root')).get_direct_response(
                prompt=prompt)
            data = extract_json_object(response[0] if response else '')
        except Exception as error:
            _logger.warning('AI customer sharing row failed for account %s: %s', account.id, error)
            raise UserError(_('AI could not prepare the customer row for %s.', account.display_name))
        if not isinstance(data, dict):
            raise UserError(_('AI returned an invalid customer row for %s.', account.display_name))
        values = {
            'share_id': self.id,
            'account_id': account.id,
            'customer_name': str(data.get('customer_name') or account.partner_id.name or '')[:250],
            'last_contact': str(data.get('last_contact') or account.last_touch_date or '')[:250],
            'contact_result': str(data.get('contact_result') or '')[:2000],
            'adoption_percent': self._safe_percent(
                data.get('adoption_percent'), account.latest_adoption_score),
            'customer_voice': str(data.get('customer_voice') or '')[:2000],
            'relationship_health_percent': self._safe_percent(
                data.get('relationship_health_percent'), account.health_score),
        }
        return values

    def action_prepare_with_ai(self):
        self.ensure_one()
        if not self.account_ids:
            raise UserError(_('Select at least one customer before preparing the table.'))
        selected = self._analyse_requested_fields()
        self.line_ids.unlink()
        for account in self.account_ids:
            self.env['cs.ai.customer.share.line'].create(
                self._prepare_account_row(account, selected))
        flags = {
            'include_%s' % field_name: field_name in selected
            for field_name in ALLOWED_SHARE_FIELDS
        }
        self.write({
            **flags,
            'state': 'prepared',
            'selected_fields': ', '.join(selected),
            'prepared_by_id': self.env.user.id,
            'prepared_on': fields.Datetime.now(),
            'approved_by_id': False,
            'approved_on': False,
        })
        return {'type': 'ir.actions.client', 'tag': 'reload'}

    def action_approve(self):
        self.ensure_one()
        if self.state != 'prepared' or not self.line_ids:
            raise UserError(_('Prepare and review the AI table before approval.'))
        self.write({
            'state': 'approved',
            'approved_by_id': self.env.user.id,
            'approved_on': fields.Datetime.now(),
        })

    def action_revoke_approval(self):
        self.write({
            'state': 'prepared' if self.line_ids else 'draft',
            'approved_by_id': False,
            'approved_on': False,
            'portal_enabled': False,
            'published_by_id': False,
            'published_on': False,
        })

    def action_publish_portal(self):
        self.ensure_one()
        if self.state != 'approved' or not self.line_ids:
            raise UserError(_('Approve the reviewed AI table before publishing it to Portal.'))
        if self.expires_on < fields.Date.today():
            raise UserError(_('Portal expiration must be today or later.'))
        self.write({
            'portal_enabled': True,
            'published_by_id': self.env.user.id,
            'published_on': fields.Datetime.now(),
        })

    def action_unpublish_portal(self):
        self.write({
            'portal_enabled': False,
            'published_by_id': False,
            'published_on': False,
        })

    def action_regenerate_portal_token(self):
        self.write({
            'access_token': secrets.token_urlsafe(32),
            'portal_enabled': False,
            'published_by_id': False,
            'published_on': False,
        })


class CsAiCustomerShareLine(models.Model):
    _name = 'cs.ai.customer.share.line'
    _description = 'AI Customer Sharing Table Row'
    _order = 'customer_name, id'

    share_id = fields.Many2one(
        'cs.ai.customer.share', required=True, ondelete='cascade')
    account_id = fields.Many2one('cs.account', required=True, ondelete='cascade')
    customer_name = fields.Char(string='Customer Name')
    last_contact = fields.Char(string='Last Contact')
    contact_result = fields.Text(string='Contact Result')
    adoption_percent = fields.Float(string='Adoption (%)')
    customer_voice = fields.Text(string='Voice of Customer')
    relationship_health_percent = fields.Float(string='Relationship Health (%)')

    def write(self, vals):
        result = super().write(vals)
        approved = self.mapped('share_id').filtered(lambda share: share.state == 'approved')
        if approved:
            approved.action_revoke_approval()
        return result

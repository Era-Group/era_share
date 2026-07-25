import json
import logging
import re
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
    ], string='Status', default='draft', required=True, readonly=True)
    selected_fields = fields.Char(string='Selected Fields', readonly=True, copy=False)
    include_customer_name = fields.Boolean(readonly=True, copy=False)
    include_last_contact = fields.Boolean(readonly=True, copy=False)
    include_contact_result = fields.Boolean(readonly=True, copy=False)
    include_adoption_percent = fields.Boolean(readonly=True, copy=False)
    include_customer_voice = fields.Boolean(readonly=True, copy=False)
    include_relationship_health_percent = fields.Boolean(readonly=True, copy=False)
    line_ids = fields.One2many(
        'cs.ai.customer.share.line', 'share_id', string='AI-Prepared Customer Table',
        copy=False)
    prepared_by_id = fields.Many2one(
        'res.users', string='Prepared By', readonly=True, copy=False)
    prepared_on = fields.Datetime(string='Prepared On', readonly=True, copy=False)
    approved_by_id = fields.Many2one(
        'res.users', string='Approved By', readonly=True, copy=False)
    approved_on = fields.Datetime(string='Approved On', readonly=True, copy=False)
    portal_enabled = fields.Boolean(string='Portal Enabled', readonly=True, copy=False)
    portal_user_ids = fields.Many2many(
        'res.users', 'cs_ai_customer_share_portal_user_rel',
        'share_id', 'user_id', string='Authorized Portal Users', copy=False,
        domain="[('share', '=', True)]")
    expires_on = fields.Date(
        string='Expires On',
        required=True, default=lambda self: fields.Date.add(fields.Date.today(), days=30))
    access_token = fields.Char(
        default=lambda self: secrets.token_urlsafe(32), readonly=True, copy=False)
    portal_url = fields.Char(string='Portal URL', compute='_compute_portal_url')
    published_by_id = fields.Many2one(
        'res.users', string='Published By', readonly=True, copy=False)
    published_on = fields.Datetime(string='Published On', readonly=True, copy=False)
    access_count = fields.Integer(string='Access Count', readonly=True, copy=False)
    last_accessed_on = fields.Datetime(
        string='Last Accessed On', readonly=True, copy=False)
    last_auto_refresh_on = fields.Datetime(
        string='Last Automatic Refresh', readonly=True, copy=False)

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
            'era_customer_success.cs_share_row_builder_agent_v3',
            raise_if_not_found=False)
        if not agent:
            raise UserError(_('The AI customer-table builder is not available.'))
        prompt = json.dumps({
            'selected_fields': selected,
            'requested_description': self.request_description,
            'customer_snapshot': self._build_share_context(account),
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
        missing = self._missing_selected_values(values, selected, account)
        if missing:
            retry_prompt = json.dumps({
                'selected_fields': selected,
                'requested_description': self.request_description,
                'customer_snapshot': self._build_share_context(account),
                'previous_response': data,
                'missing_fields': missing,
                'instruction': (
                    'Return every selected field. contact_result and customer_voice must each '
                    'contain Arabic and English together. Use an explicit bilingual no-evidence '
                    'statement instead of blank text.'),
            }, ensure_ascii=False)
            try:
                response = agent.with_user(self.env.ref('base.user_root')).get_direct_response(
                    prompt=retry_prompt)
                retry_data = extract_json_object(response[0] if response else '')
            except Exception as error:
                _logger.warning('AI customer sharing retry failed for account %s: %s', account.id, error)
                retry_data = None
            if isinstance(retry_data, dict):
                values.update({
                    'customer_name': str(retry_data.get('customer_name') or values['customer_name'])[:250],
                    'last_contact': str(retry_data.get('last_contact') or values['last_contact'])[:250],
                    'contact_result': str(retry_data.get('contact_result') or values['contact_result'])[:2000],
                    'adoption_percent': self._safe_percent(
                        retry_data.get('adoption_percent'), values['adoption_percent']),
                    'customer_voice': str(retry_data.get('customer_voice') or values['customer_voice'])[:2000],
                    'relationship_health_percent': self._safe_percent(
                        retry_data.get('relationship_health_percent'), values['relationship_health_percent']),
                })
        self._complete_missing_values(values, account, selected)
        return values

    def _build_share_context(self, account):
        lines = [
            'Customer: %s' % account.partner_id.name,
            'Lifecycle stage: %s' % (account.lifecycle_stage_id.name or 'Not recorded'),
            'Current relationship health signal: %s/100 (%s)' % (
                account.health_score, account.health_status or 'unknown'),
            'Usage signal: %s' % (account.usage_signal or 'not recorded'),
            'Last tracked touch: %s; next planned touch: %s' % (
                account.last_touch_date or 'not recorded', account.next_touch_date or 'not recorded'),
            'Customer sentiment signal: %s (%s)' % (
                account.sentiment_label or 'not recorded', account.sentiment_score or 0),
            'Interaction counts: calls %s, WhatsApp %s, meetings %s' % (
                account.call_count, account.whatsapp_count, account.meeting_count),
        ]
        adoption = self.env['cs.adoption.assessment'].sudo().search([
            ('cs_account_id', '=', account.id), ('state', '=', 'confirmed')],
            order='assessment_date desc, id desc', limit=1)
        if adoption:
            lines.append('Confirmed adoption assessment on %s: %s/100; status %s; blockers %s' % (
                adoption.assessment_date, adoption.score, adoption.status,
                (adoption.blockers or 'none recorded')[:500]))
        else:
            lines.append('No confirmed adoption assessment. Estimate adoption from current usage, lifecycle, and interaction evidence.')
        completed = self.env['cs.weekly.suggestion'].sudo().search([
            ('cs_account_id', '=', account.id), ('state', '=', 'done')],
            order='completed_on desc, id desc', limit=3)
        if completed:
            lines.append('Recent completed follow-up outcomes:')
            lines.extend('- %s | %s | %s | next: %s' % (
                item.completed_on or item.write_date,
                item.outcome or 'not classified',
                (item.outcome_note or 'no result note')[:500],
                item.next_step or 'not recorded') for item in completed)
        voc = self.env['cs.voc.insight'].sudo().search([
            ('cs_account_id', '=', account.id),
            ('state', 'not in', ('dismissed',))],
            order='insight_date desc, id desc', limit=3)
        if voc:
            lines.append('Voice of Customer evidence:')
            lines.extend('- %s | %s | %s | %s' % (
                item.insight_date, item.sentiment, item.theme,
                (item.summary or 'no summary')[:500]) for item in voc)
        messages = account.message_ids.filtered(lambda message: message.body).sorted(
            key=lambda message: message.date or message.create_date, reverse=True)[:10]
        if messages:
            lines.append('Recent dated timeline:')
            for message in messages:
                body = re.sub(r'<[^>]+>', ' ', message.body or '')
                body = re.sub(r'\s+', ' ', body).strip()
                if body:
                    lines.append('- %s | %s' % (
                        message.date or message.create_date, body[:500]))
        return '\n'.join(lines)

    @api.model
    def _missing_selected_values(self, values, selected, account=None):
        missing = [field_name for field_name in selected
                   if field_name in ('last_contact', 'contact_result', 'customer_voice')
                   and not str(values.get(field_name) or '').strip()]
        missing.extend(
            field_name for field_name in ('contact_result', 'customer_voice')
            if field_name in selected and field_name not in missing
            and not self._is_bilingual_text(values.get(field_name)))
        if (account and 'adoption_percent' in selected
                and not values.get('adoption_percent')
                and (account.usage_signal == 'active' or account.call_count
                     or account.whatsapp_count or account.meeting_count)):
            missing.append('adoption_percent')
        if (account and 'relationship_health_percent' in selected
                and not values.get('relationship_health_percent')
                and account.health_score):
            missing.append('relationship_health_percent')
        return missing

    @api.model
    def _is_bilingual_text(self, value):
        text = str(value or '')
        return bool(re.search(r'[\u0600-\u06ff]', text) and re.search(r'[A-Za-z]', text))

    @api.model
    def _complete_missing_values(self, values, account, selected):
        if 'last_contact' in selected and not values.get('last_contact'):
            latest_message = account.message_ids.filtered(lambda message: message.body).sorted(
                key=lambda message: message.date or message.create_date, reverse=True)[:1]
            values['last_contact'] = str(
                account.last_touch_date or
                (latest_message.date or latest_message.create_date if latest_message else '') or
                'لا يوجد تواصل موثق مع العميل. / No verified customer contact is recorded.')[:250]
        if ('contact_result' in selected
                and not self._is_bilingual_text(values.get('contact_result'))):
            values['contact_result'] = (
                'لا توجد نتيجة تواصل موثقة؛ يلزم المراجعة. / '
                'No verified contact result is recorded; review is required.')
        if ('customer_voice' in selected
                and not self._is_bilingual_text(values.get('customer_voice'))):
            if account.sentiment_label:
                values['customer_voice'] = (
                    'لا يوجد تصريح مباشر مسجل لصوت العميل. إشارة المشاعر الحالية: %(signal)s. / '
                    'No direct Voice of Customer statement is recorded. Current sentiment signal: %(signal)s.'
                ) % {'signal': account.sentiment_label}
            else:
                values['customer_voice'] = (
                    'لا يوجد تصريح مباشر مسجل لصوت العميل. / '
                    'No direct Voice of Customer statement is recorded.')

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
            'portal_enabled': False,
            'published_by_id': False,
            'published_on': False,
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

    def _refresh_approved_portal_table(self, respect_portal_selection=False):
        self.ensure_one()
        if self.state != 'approved' or not self.portal_enabled:
            return False
        selected = self._filter_allowed_fields(
            [field_name.strip() for field_name in (self.selected_fields or '').split(',')])
        if not selected:
            raise UserError(_('The approved Portal table has no safe selected fields.'))
        if not self.account_ids:
            raise UserError(_('The approved Portal table has no included customers.'))
        accounts = self.account_ids
        if respect_portal_selection:
            accounts = accounts.filtered('send_to_portal_share')
        # Prepare all rows first so an AI failure cannot erase the currently published table.
        row_values = [self._prepare_account_row(account, selected)
                      for account in accounts]
        refresh_context = dict(self.env.context, cs_scheduled_portal_refresh=True)
        self.line_ids.with_context(refresh_context).unlink()
        if row_values:
            self.env['cs.ai.customer.share.line'].with_context(refresh_context).create(row_values)
        self.with_context(refresh_context).write({
            'prepared_by_id': self.env.user.id,
            'prepared_on': fields.Datetime.now(),
            'last_auto_refresh_on': fields.Datetime.now(),
        })
        return True

    @api.model
    def _cron_refresh_approved_portal_tables(self, limit=50):
        shares = self.sudo().search([
            ('state', '=', 'approved'),
            ('portal_enabled', '=', True),
            ('expires_on', '>=', fields.Date.today()),
        ], order='last_auto_refresh_on asc, id', limit=limit)
        for share in shares:
            try:
                with self.env.cr.savepoint():
                    share._refresh_approved_portal_table(respect_portal_selection=True)
            except Exception as error:
                _logger.exception(
                    'Scheduled Portal refresh kept the previous table for share %s: %s',
                    share.id, error)
        return True


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

    def unlink(self):
        if self.env.context.get('cs_scheduled_portal_refresh'):
            return super().unlink()
        approved = self.mapped('share_id').filtered(lambda share: share.state == 'approved')
        result = super().unlink()
        if approved:
            approved.action_revoke_approval()
        return result

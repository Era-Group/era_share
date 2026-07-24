# -*- coding: utf-8 -*-
import logging

from odoo import api, fields, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class CsmOffering(models.Model):
    _name = 'csm.offering'
    _description = 'Customer Success Offering'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'offering_date desc, id desc'
    _check_company_auto = True

    name = fields.Char(string='Offering', required=True, tracking=True)
    cs_account_id = fields.Many2one('cs.account', string='Customer Success Account',
                                    ondelete='cascade', check_company=True)
    company_id = fields.Many2one('res.company', required=True, index=True,
                                 default=lambda self: self.env.company)
    partner_id = fields.Many2one('res.partner', string='Customer', required=True, tracking=True)
    csm_user_id = fields.Many2one(
        'res.users', string='Presented By', default=lambda self: self.env.user, tracking=True)
    offering_date = fields.Date(default=fields.Date.context_today, tracking=True)
    service_id = fields.Many2one('cs.service', string='Catalog Service')
    service_image = fields.Image(
        related='service_id.image_1920', string='Service Image', readonly=True)
    pitch_message = fields.Text(string='Suggested Pitch (AI)')
    product_tmpl_ids = fields.Many2many('product.template', string='Products / Services')
    expected_value = fields.Monetary(string='Expected Value', currency_field='currency_id')
    currency_id = fields.Many2one(
        'res.currency', default=lambda self: self.env.company.currency_id)
    channel = fields.Selection([
        ('whatsapp', 'WhatsApp'),
        ('email', 'Email'),
        ('call', 'Call'),
        ('meeting', 'Meeting'),
        ('other', 'Other'),
    ], default='whatsapp', string='Presented Via')
    state = fields.Selection([
        ('draft', 'Draft'),
        ('presented', 'Presented'),
        ('accepted', 'Accepted'),
        ('rejected', 'Rejected'),
    ], default='draft', tracking=True, string='Status')
    notes = fields.Text()
    opportunity_id = fields.Many2one('crm.lead', string='Opportunity', readonly=True)
    is_service_recommendation = fields.Boolean(readonly=True, copy=False)
    recommendation_key = fields.Char(readonly=True, copy=False, index=True)
    recommendation_score = fields.Integer(readonly=True, copy=False)
    recommendation_reason = fields.Text(readonly=True, copy=False)

    _recommendation_key_unique = models.Constraint(
        'unique(recommendation_key)',
        'This service recommendation already exists.')

    @api.onchange('service_id')
    def _onchange_service_id(self):
        if self.service_id:
            if not self.name:
                self.name = self.service_id.name
            if not self.pitch_message and self.service_id.pitch_template:
                self.pitch_message = self.service_id.pitch_template
            if self.service_id.product_tmpl_ids:
                self.product_tmpl_ids = [(6, 0, self.service_id.product_tmpl_ids.ids)]

    def action_suggest_pitch(self):
        """Generate a customer-tailored pitch for this offering using AI."""
        agent = self.env.ref('era_customer_success.cs_offering_pitch_agent', raise_if_not_found=False)
        if not agent:
            raise UserError(_('The pitch AI agent is not available.'))
        root = self.env.ref('base.user_root')
        for off in self:
            svc = off.service_id
            if svc:
                service_txt = "Service: %s\nDescription: %s\nFeatures:\n%s\nGeneric pitch: %s" % (
                    svc.name, svc.short_description or '', svc.features or '',
                    svc.pitch_template or '')
            else:
                service_txt = "Offering: %s" % (off.name or '')
            if off.cs_account_id:
                customer_txt = off.cs_account_id._build_situation_summary()
            else:
                customer_txt = "Customer: %s" % (off.partner_id.name or '')
            prompt = "=== SERVICE ===\n%s\n\n=== CUSTOMER ===\n%s" % (service_txt, customer_txt)
            try:
                response = agent.with_user(root).get_direct_response(prompt=prompt)
                message = (response[0] if response else '') or ''
            except Exception:
                _logger.exception('Pitch generation failed for offering %s', off.id)
                raise UserError(_('AI pitch generation failed. Check the AI provider configuration.'))
            off.pitch_message = message.strip()
        return True

    @api.onchange('cs_account_id')
    def _onchange_cs_account_id(self):
        if self.cs_account_id:
            self.company_id = self.cs_account_id.company_id
            if not self.partner_id:
                self.partner_id = self.cs_account_id.partner_id
            if not self.csm_user_id:
                self.csm_user_id = self.cs_account_id.csm_user_id

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('cs_account_id'):
                account = self.env['cs.account'].browse(vals['cs_account_id'])
                vals.setdefault('partner_id', account.partner_id.id)
                vals.setdefault('csm_user_id', account.csm_user_id.id)
                vals.setdefault('company_id', account.company_id.id)
        return super().create(vals_list)

    @api.constrains('cs_account_id', 'partner_id', 'company_id')
    def _check_account_customer_company(self):
        for offering in self.filtered('cs_account_id'):
            same_company = offering.company_id == offering.cs_account_id.company_id
            same_customer = (
                offering.partner_id.commercial_partner_id
                == offering.cs_account_id.partner_id.commercial_partner_id)
            if not same_company or not same_customer:
                raise UserError(_(
                    'The offering customer and company must match the Customer Success account.'))

    def action_present(self):
        self.write({'state': 'presented'})
        for off in self:
            off.message_post(body=_('Offering "%s" presented to the customer.', off.name))
        return True

    def action_accept(self):
        upsell_tag = self.env.ref('era_customer_success.crm_tag_cs_upsell', raise_if_not_found=False)
        for off in self:
            lead = self.env['crm.lead'].create({
                'name': _('Upsell: %s', off.name),
                'type': 'opportunity',
                'partner_id': off.partner_id.id,
                'user_id': off.csm_user_id.id,
                'expected_revenue': off.expected_value,
                'cs_is_upsell': True,
                'cs_upsell_type': 'expansion',
                'tag_ids': [(4, upsell_tag.id)] if upsell_tag else False,
            })
            off.write({'state': 'accepted', 'opportunity_id': lead.id})
            off.message_post(body=_('Offering accepted – opportunity %s created.', lead.name))
        return True

    def action_reject(self):
        self.write({'state': 'rejected'})
        return True

    def action_send_service_whatsapp(self):
        """Send this offering's service WhatsApp template to the customer.

        Service templates are bound to the Customer Success model (cs.account),
        so the send is opened on THIS offering's linked cs.account record (same
        customer) — the model the template belongs to — with the service
        template preselected. The template must be Meta-approved to actually
        send."""
        self.ensure_one()
        if not self.service_id:
            raise UserError(_('Set the catalog service on this offering first.'))
        tmpl = self.service_id.whatsapp_template_id
        if not tmpl:
            raise UserError(_(
                'No WhatsApp template for "%s" yet — create it from the service '
                'catalog (button "إنشاء قالب واتساب").', self.service_id.name))
        if not self.cs_account_id:
            raise UserError(_('This offering has no linked Customer Success account.'))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Send WhatsApp'),
            'res_model': 'whatsapp.composer',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'active_model': 'cs.account',
                'active_id': self.cs_account_id.id,
                'active_ids': [self.cs_account_id.id],
                'default_wa_template_id': tmpl.id,
            },
        }

    def action_send_service_email(self):
        """Email the customer the SAME content as the service WhatsApp template
        (image + body + "اعرف المزيد" link). Opens the mail composer in
        'comment' mode on the linked cs.account record so it both logs on the
        account chatter and emails the customer."""
        self.ensure_one()
        if not self.service_id:
            raise UserError(_('Set the catalog service on this offering first.'))
        if not self.cs_account_id:
            raise UserError(_('This offering has no linked Customer Success account.'))
        partner = self.partner_id
        if not partner or not partner.email:
            raise UserError(_('The customer has no email address.'))
        svc = self.service_id
        return {
            'name': _('Send Email'),
            'type': 'ir.actions.act_window',
            'res_model': 'mail.compose.message',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_model': 'cs.account',
                'default_res_ids': [self.cs_account_id.id],
                'default_composition_mode': 'comment',
                'default_subject': svc._build_email_subject(),
                'default_body': svc._build_email_body(),
                'default_partner_ids': [partner.id],
                'mail_post_autofollow': True,
            },
        }

    def action_open_opportunity(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'crm.lead',
            'res_id': self.opportunity_id.id,
            'view_mode': 'form',
        }

    def action_open_form(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.name or _('Offering'),
            'res_model': 'csm.offering',
            'res_id': self.id,
            'view_mode': 'form',
            'views': [(False, 'form')],
            'target': 'current',
        }

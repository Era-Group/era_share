# -*- coding: utf-8 -*-
import base64
import logging
import re

import requests

from markupsafe import Markup

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError
from odoo.tools import html2plaintext
from odoo.tools.mimetypes import guess_mimetype

from .cs_account import _cs_extract_json

_logger = logging.getLogger(__name__)

# mimetype -> clean extension (used so a message attachment keeps a clean, well-known
# file extension when the service image is sent alongside a follow-up message).
_CS_IMG_EXT = {
    'image/png': '.png',
    'image/jpeg': '.jpg',
    'image/gif': '.gif',
    'image/webp': '.webp',
    'image/bmp': '.bmp',
}


class CsService(models.Model):
    _name = 'cs.service'
    _description = 'Customer Success Service Catalog'
    _order = 'name'
    _inherit = ['mail.thread', 'image.mixin']

    name = fields.Char(string='Service', required=True, tracking=True)
    active = fields.Boolean(default=True)
    color = fields.Integer()
    url = fields.Char(string='Service URL', help="Public page describing the service.")
    short_description = fields.Text(string='Description')
    features = fields.Text(string='Key Features / Benefits')
    product_details = fields.Text(string='Product Details')
    target_audience = fields.Char(string='Target Audience')
    decision_points = fields.Text(
        string='Decision-support Notes',
        help="Pricing model, value proposition and anything that helps decide whether "
             "to offer this to a customer.")
    pitch_template = fields.Text(string='Suggested Pitch')
    product_tmpl_ids = fields.Many2many('product.template', string='Related Products')
    service_type = fields.Selection([
        ('service', 'Service'),
        ('module', 'ERA Module'),
        ('training', 'Training'),
        ('advisory', 'Advisory'),
        ('integration', 'Integration'),
    ], default='service', required=True)
    need_signals = fields.Text(
        string='Need Signals',
        help='Observable customer conditions that indicate this service may create value.')
    discovery_questions = fields.Text(
        string='Discovery Questions',
        help='Questions the CSM should ask before qualifying this need.')
    value_outcomes = fields.Text(
        string='Expected Customer Outcomes')
    not_suitable_when = fields.Text(
        string='Not Suitable When',
        help='Conditions that should stop the CSM from presenting this service.')
    recommend_on_low_adoption = fields.Boolean(string='Recommend for Low Adoption')
    recommend_on_support_pressure = fields.Boolean(string='Recommend for Support Pressure')
    recommend_on_sla_failure = fields.Boolean(string='Recommend after Failed SLA')
    recommendation_ticket_tag_ids = fields.Many2many(
        'helpdesk.tag', 'cs_service_helpdesk_tag_rel',
        'service_id', 'tag_id', string='Recommendation Ticket Tags')
    suggested_ticket_tags = fields.Text(
        string='Suggested Ticket Tags', readonly=True,
        help='AI suggestions for matching Helpdesk tags. Review and select real database tags before applying them.')
    recommendation_cooldown_days = fields.Integer(
        string='Re-offer Cooldown (Days)', default=90)
    ai_enriched_on = fields.Datetime(string='AI Enriched On', readonly=True)
    offering_count = fields.Integer(compute='_compute_offering_count', string='# Offerings')
    whatsapp_template_id = fields.Many2one(
        'whatsapp.template', string='WhatsApp Template', readonly=True, copy=False,
        help="The Meta WhatsApp message template generated for this service.")

    def _compute_offering_count(self):
        data = self.env['csm.offering']._read_group(
            [('service_id', 'in', self.ids)], groupby=['service_id'], aggregates=['__count'])
        mapped = {s.id: c for s, c in data}
        for svc in self:
            svc.offering_count = mapped.get(svc.id, 0)

    @api.constrains('recommendation_cooldown_days')
    def _check_recommendation_cooldown(self):
        for service in self:
            if service.recommendation_cooldown_days < 0:
                raise ValidationError(_('Recommendation cooldown cannot be negative.'))

    def action_enrich_from_url(self):
        """Fetch the service page and let the AI extract structured details."""
        agent = self.env.ref('era_customer_success.cs_service_extract_agent', raise_if_not_found=False)
        if not agent:
            raise UserError(_('The service-extraction AI agent is not available.'))
        root = self.env.ref('base.user_root')
        for svc in self:
            if not svc.url:
                raise UserError(_('Please set the service URL first.'))
            try:
                resp = requests.get(
                    svc.url, timeout=20,
                    headers={'User-Agent': 'Mozilla/5.0 (compatible; OdooCS/1.0)'})
                resp.raise_for_status()
                page_text = html2plaintext(resp.text)[:6000]
            except Exception as e:
                raise UserError(_('Could not fetch the URL: %s', e))
            if not page_text.strip():
                raise UserError(_('The page returned no readable text.'))
            try:
                response = agent.with_user(root).get_direct_response(
                    prompt="URL: %s\n\n%s" % (svc.url, page_text))
                data = _cs_extract_json(response[0] if response else '')
            except Exception:
                _logger.exception('Service extraction failed for %s', svc.id)
                raise UserError(_('AI extraction failed. Check the AI provider configuration.'))
            if not isinstance(data, dict):
                raise UserError(_('The AI did not return usable data.'))
            recommendation_flags = {
                field_name: data.get(field_name) is True
                for field_name in (
                    'recommend_on_low_adoption',
                    'recommend_on_support_pressure',
                    'recommend_on_sla_failure',
                )
                if data.get(field_name) is not None
            }
            recommendation_rationale = (data.get('recommendation_rationale') or '').strip()
            decision_points = data.get('decision_points') or svc.decision_points
            if recommendation_rationale:
                decision_points = '%s\n\n%s: %s' % (
                    decision_points or '',
                    _('Recommendation setup rationale'),
                    recommendation_rationale,
                )
            svc.write({
                'short_description': data.get('description') or svc.short_description,
                'features': data.get('features') or svc.features,
                'product_details': data.get('product_details') or svc.product_details,
                'target_audience': data.get('target_audience') or svc.target_audience,
                'decision_points': decision_points,
                'pitch_template': data.get('suggested_pitch') or svc.pitch_template,
                'need_signals': data.get('need_signals') or svc.need_signals,
                'discovery_questions': data.get('discovery_questions') or svc.discovery_questions,
                'value_outcomes': data.get('value_outcomes') or svc.value_outcomes,
                'not_suitable_when': data.get('not_suitable_when') or svc.not_suitable_when,
                'suggested_ticket_tags': data.get('suggested_ticket_tags') or svc.suggested_ticket_tags,
                'ai_enriched_on': fields.Datetime.now(),
                **recommendation_flags,
            })
            # AI enrichment is NOT posted to the chatter — the extracted details and
            # the "AI Enriched On" timestamp are shown in the form fields instead.
        return True

    def action_apply_suggested_ticket_tags(self):
        """Apply only exact suggestions that match existing Helpdesk tags."""
        Tag = self.env['helpdesk.tag']
        for service in self:
            names = [
                line.strip().lstrip('-').strip()
                for line in (service.suggested_ticket_tags or '').splitlines()
                if line.strip()
            ]
            tags = Tag.search([('name', 'in', names)])
            service.recommendation_ticket_tag_ids = [(6, 0, tags.ids)]
        return True

    def action_view_offerings(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Offerings'),
            'res_model': 'csm.offering',
            'view_mode': 'list,form',
            'domain': [('service_id', '=', self.id)],
            'context': {'default_service_id': self.id},
        }

    # ------------------------------------------------------------------
    # Image-as-media helpers (used to attach the catalog image to messages)
    # ------------------------------------------------------------------
    def _cs_image_mimetype(self):
        self.ensure_one()
        if not self.image_1920:
            return False
        return guess_mimetype(base64.b64decode(self.image_1920)) or 'image/png'

    def _cs_image_filename(self):
        """Sanitised filename with a clean image extension."""
        self.ensure_one()
        ext = _CS_IMG_EXT.get(self._cs_image_mimetype(), '.png')
        base = re.sub(r'[^\w\-]+', '_', (self.name or 'service')).strip('_') or 'service'
        return base + ext

    def _cs_message_attachment(self):
        """An ir.attachment (binary) built from this service's image, for use as
        message media / email attachment. Empty recordset when no image is set."""
        self.ensure_one()
        if not self.image_1920:
            return self.env['ir.attachment']
        return self.env['ir.attachment'].create({
            'name': self._cs_image_filename(),
            'datas': self.image_1920,
            'mimetype': self._cs_image_mimetype() or 'image/png',
            'res_model': self._name,
            'res_id': self.id,
        })

    # ------------------------------------------------------------------
    # WhatsApp template (Meta-approved) — one per service
    # ------------------------------------------------------------------
    def _build_whatsapp_body(self):
        """Plain-text marketing body for the WhatsApp template (Meta limit 1024).
        No variables -> nothing to map and easiest to get approved."""
        self.ensure_one()
        lines = ['يسعدنا أن نقدّم لكم خدمة *%s* من مجموعة إيرا.' % (self.name or '').strip()]
        desc = html2plaintext(self.short_description or '').strip()
        if desc:
            lines += ['', desc]
        feats = html2plaintext(self.features or '').strip()
        if feats:
            lines += ['', '✦ أبرز المميزات:', feats]
        body = re.sub(r'\n{3,}', '\n\n', '\n'.join(lines)).strip()
        return body[:1024]

    def _build_email_subject(self):
        self.ensure_one()
        return _('خدمة %s من مجموعة إيرا', self.name or '')

    def _build_email_body(self):
        """HTML email mirroring the WhatsApp template content (image header,
        body, "اعرف المزيد" button, footer). The image is embedded as a base64
        data URI — Odoo's mail.message.create() turns it into an inline
        attachment when the message is sent, so it renders in email clients."""
        self.ensure_one()
        # WhatsApp plain body (with *bold* and newlines) -> safe HTML
        body = str(Markup.escape(self._build_whatsapp_body()))
        body = re.sub(r'\*([^*]+)\*', r'<b>\1</b>', body).replace('\n', '<br/>')
        image_html = ''
        # Use a resized copy (≈1024px) for the email header — image_1920 base64
        # would bloat the body to ~MBs and slow the composer.
        img = self.image_1024 or self.image_1920
        if img:
            b64 = img.decode('ascii') if isinstance(img, bytes) else img
            try:
                mimetype = guess_mimetype(base64.b64decode(img)) or 'image/png'
            except Exception:
                mimetype = 'image/png'
            image_html = (
                '<div style="text-align:center;margin-bottom:16px">'
                '<img src="data:%s;base64,%s" alt="%s" data-filename="%s" '
                'style="max-width:100%%;height:auto;border-radius:8px"/></div>'
                % (mimetype, b64, Markup.escape(self.name or ''),
                   Markup.escape(self._cs_image_filename()))
            )
        button_html = ''
        if self.url:
            button_html = (
                '<div style="margin:18px 0"><a href="%s" '
                'style="display:inline-block;background:#7b2d8e;color:#fff;padding:10px 22px;'
                'border-radius:6px;text-decoration:none;font-weight:bold">اعرف المزيد</a></div>'
                % Markup.escape(self.url)
            )
        full = (
            '<div dir="rtl" style="max-width:600px;margin:0 auto;'
            'font-family:Arial,sans-serif;color:#333;font-size:15px;line-height:1.7">'
            '%s<div>%s</div>%s'
            '<div style="margin-top:18px;color:#999;font-size:12px">مجموعة إيرا</div></div>'
            % (image_html, body, button_html)
        )
        return Markup(full)

    def action_create_whatsapp_template(self):
        """Create (or open) a Meta WhatsApp template for this service. Created as a
        DRAFT for review — submit it to Meta from the template form."""
        self.ensure_one()
        Template = self.env['whatsapp.template']
        tmpl = self.whatsapp_template_id or Template.search(
            [('template_name', '=', 'cs_service_%s' % self.id)], limit=1)
        if not tmpl:
            account = self.env['whatsapp.account'].search([], limit=1)
            if not account:
                raise UserError(_('No WhatsApp Business account is configured.'))
            vals = {
                'name': (self.name or 'Service')[:512],
                'template_name': 'cs_service_%s' % self.id,
                'lang_code': 'ar',
                'template_type': 'marketing',
                'wa_account_id': account.id,
                'model_id': self.env['ir.model']._get('cs.account').id,
                'phone_field': 'phone',
                'body': self._build_whatsapp_body(),
                'footer_text': 'مجموعة إيرا',
            }
            # Image header: the attachment is required at create time (constraint),
            # so build it first and pass it in the create vals; link res_id after.
            header_att = self.env['ir.attachment']
            if self.image_1920:
                header_att = self.env['ir.attachment'].create({
                    'name': self._cs_image_filename(),
                    'datas': self.image_1920,
                    'mimetype': self._cs_image_mimetype() or 'image/png',
                })
                vals['header_type'] = 'image'
                vals['header_attachment_ids'] = [(6, 0, header_att.ids)]
            elif self.name:
                vals['header_type'] = 'text'
                vals['header_text'] = self.name[:60]
            tmpl = Template.create(vals)
            if header_att:
                header_att.write({'res_model': 'whatsapp.template', 'res_id': tmpl.id})
            if self.url:
                self.env['whatsapp.template.button'].create({
                    'wa_template_id': tmpl.id,
                    'name': 'اعرف المزيد',
                    'button_type': 'url',
                    'url_type': 'static',
                    'website_url': self.url,
                    'sequence': 1,
                })
        self.whatsapp_template_id = tmpl.id
        return {
            'type': 'ir.actions.act_window',
            'name': _('WhatsApp Template'),
            'res_model': 'whatsapp.template',
            'res_id': tmpl.id,
            'view_mode': 'form',
            'target': 'current',
        }

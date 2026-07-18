# -*- coding: utf-8 -*-
import logging

from odoo import api, fields, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class CsFollowupCompose(models.TransientModel):
    _name = 'cs.followup.compose'
    _description = 'AI Follow-up / Reply Composer'

    cs_account_id = fields.Many2one('cs.account', string='Account', required=True)
    partner_id = fields.Many2one('res.partner', string='Customer', required=True)
    channel = fields.Selection([
        ('whatsapp', 'WhatsApp'),
        ('sms', 'SMS'),
        ('email', 'Email'),
    ], string='Channel', default='whatsapp', required=True)
    tone = fields.Selection([
        ('friendly', 'Friendly'),
        ('formal', 'Formal'),
        ('concise', 'Concise'),
        ('warm', 'Warm / Empathetic'),
    ], string='Tone', default='friendly', required=True)
    purpose = fields.Char(
        string='Purpose / Intent',
        help="What this message is about, e.g. periodic check-in, renewal nudge, "
             "thank-you after resolution. Leave empty for a general follow-up.")
    reply_to_last = fields.Boolean(
        string='Reply to last message',
        help="Draft a reply to the customer's most recent message using the recent timeline.")
    service_id = fields.Many2one(
        'cs.service', string='Attach Service', domain="[('active', '=', True)]",
        help="Optionally attach a catalog service. Its image is sent with the message "
             "(WhatsApp / email) and its details steer the AI draft.")
    service_image = fields.Image(
        related='service_id.image_1920', string='Service Image', readonly=True)
    draft = fields.Text(string='Draft Message')

    # ------------------------------------------------------------------
    def _build_compose_prompt(self):
        self.ensure_one()
        acc = self.cs_account_id
        channel_label = dict(self._fields['channel'].selection).get(self.channel)
        tone_label = dict(self._fields['tone'].selection).get(self.tone)
        parts = [acc._build_situation_summary()]
        parts.append("\n=== REQUEST ===")
        parts.append("Channel: %s" % channel_label)
        parts.append("Tone: %s" % tone_label)
        if self.purpose:
            parts.append("Purpose: %s" % self.purpose)
        if self.service_id:
            svc = self.service_id
            parts.append(
                "Service to highlight: %s\nDescription: %s\nKey features: %s" % (
                    svc.name, (svc.short_description or '-'), (svc.features or '-')))
            parts.append("An image of this service is attached to the message; you may "
                         "refer to it briefly (do not paste a URL).")
        if self.reply_to_last:
            parts.append("TASK: Draft a reply to the customer's most recent message, "
                         "using the recent interaction history above.")
        else:
            parts.append("TASK: Draft a proactive follow-up message.")
        return "\n".join(parts)

    def action_generate(self):
        self.ensure_one()
        agent = self.env.ref('era_customer_success.cs_followup_draft_agent',
                             raise_if_not_found=False)
        if not agent:
            raise UserError(_('The follow-up composer AI agent is not available.'))
        root = self.env.ref('base.user_root')
        try:
            response = agent.with_user(root).get_direct_response(prompt=self._build_compose_prompt())
        except Exception:
            _logger.exception("CS follow-up draft failed for account %s", self.cs_account_id.id)
            raise UserError(_('Could not generate a draft right now. Please try again.'))
        text = (response[0] if response else '') or ''
        if not text.strip():
            raise UserError(_('The AI returned an empty draft. Adjust the purpose and try again.'))
        self.draft = text.strip()
        return self._reload()

    def _reload(self):
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
            'name': _('Compose Follow-up (AI)'),
        }

    # ------------------------------------------------------------------
    def _check_draft(self):
        if not self.draft or not self.draft.strip():
            raise UserError(_('Generate or write a draft first.'))

    def _has_service_image(self):
        return bool(self.service_id and self.service_id.image_1920)

    def action_send(self):
        """Route the approved draft to the chosen channel's native composer / log."""
        self.ensure_one()
        self._check_draft()
        if self.channel == 'sms':
            # SMS cannot carry an image; the text is sent as-is.
            return {
                'type': 'ir.actions.act_window',
                'name': _('Send SMS'),
                'res_model': 'sms.composer',
                'view_mode': 'form',
                'target': 'new',
                'context': {
                    'default_res_model': 'res.partner',
                    'default_res_ids': [self.partner_id.id],
                    'default_body': self.draft,
                    'default_composition_mode': 'comment',
                    'active_model': 'res.partner',
                    'active_id': self.partner_id.id,
                },
            }
        if self.channel == 'email':
            body = self.draft
            subject = False
            if body.lower().startswith('subject:'):
                first, _sep, rest = body.partition('\n')
                subject = first.split(':', 1)[1].strip()
                body = rest.strip()
            ctx = {
                'default_model': 'res.partner',
                'default_res_ids': [self.partner_id.id],
                'default_partner_ids': [self.partner_id.id],
                'default_subject': subject or _('Follow-up from ERA'),
                'default_body': '<p>%s</p>' % body.replace('\n', '<br/>'),
                'default_composition_mode': 'comment',
            }
            if self._has_service_image():
                att = self.service_id._cs_message_attachment()
                ctx['default_attachment_ids'] = [(6, 0, att.ids)]
            return {
                'type': 'ir.actions.act_window',
                'name': _('Send Email'),
                'res_model': 'mail.compose.message',
                'view_mode': 'form',
                'target': 'new',
                'context': ctx,
            }
        # WhatsApp: route the draft to the native WhatsApp composer.
        return self.cs_account_id.action_cs_send_whatsapp()

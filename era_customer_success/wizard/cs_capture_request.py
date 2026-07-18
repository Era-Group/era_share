# -*- coding: utf-8 -*-
from odoo import api, fields, models, _


class CsCaptureRequest(models.TransientModel):
    _name = 'cs.capture.request'
    _description = 'Capture Customer Request'

    cs_account_id = fields.Many2one('cs.account', string='Account', required=True)
    partner_id = fields.Many2one('res.partner', string='Customer', required=True)
    request_type = fields.Selection([
        ('support', 'Support Issue'),
        ('upsell', 'Upsell / Cross-sell'),
        ('renewal', 'Renewal'),
    ], default='support', required=True, string='Route As')
    name = fields.Char(string='Subject', required=True)
    description = fields.Text()
    team_id = fields.Many2one('helpdesk.team', string='Helpdesk Team')
    expected_revenue = fields.Monetary(currency_field='currency_id')
    currency_id = fields.Many2one('res.currency', default=lambda self: self.env.company.currency_id)

    def action_submit(self):
        self.ensure_one()
        csm = self.cs_account_id.csm_user_id or self.env.user
        if self.request_type == 'support':
            record = self.env['helpdesk.ticket'].create({
                'name': self.name,
                'partner_id': self.partner_id.id,
                'team_id': self.team_id.id or False,
                'description': self.description or '',
                'user_id': csm.id,
            })
            res_model, label = 'helpdesk.ticket', _('Ticket')
        else:
            upsell_tag = self.env.ref(
                'era_customer_success.crm_tag_cs_upsell', raise_if_not_found=False)
            record = self.env['crm.lead'].create({
                'name': self.name,
                'type': 'opportunity',
                'partner_id': self.partner_id.id,
                'user_id': csm.id,
                'description': self.description or '',
                'expected_revenue': self.expected_revenue,
                'cs_is_upsell': True,
                'cs_upsell_type': 'renewal' if self.request_type == 'renewal' else 'expansion',
                'tag_ids': [(4, upsell_tag.id)] if upsell_tag else False,
            })
            res_model, label = 'crm.lead', _('Opportunity')

        self.cs_account_id.message_post(
            body=_('New request captured "%(name)s" → %(label)s #%(id)s',
                   name=self.name, label=label, id=record.id))
        return {
            'type': 'ir.actions.act_window',
            'name': label,
            'res_model': res_model,
            'res_id': record.id,
            'view_mode': 'form',
        }

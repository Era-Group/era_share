import secrets

from odoo import api, fields, models, _
from odoo.exceptions import UserError


class CsPortfolioShare(models.Model):
    _name = 'cs.portfolio.share'
    _description = 'External Customer Portfolio Share'
    _order = 'id desc'

    name = fields.Char(required=True, default=lambda self: _('Odoo Shared Customer Portfolio'))
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)
    active = fields.Boolean(default=False)
    access_token = fields.Char(readonly=True, copy=False, index=True, default=lambda self: secrets.token_urlsafe(32))
    expires_on = fields.Date(required=True, default=lambda self: fields.Date.add(fields.Date.today(), months=1))
    allow_export = fields.Boolean(default=True)
    portal_user_ids = fields.Many2many('res.users', 'cs_portfolio_share_user_rel', 'share_id', 'user_id', string='Authorized Portal Users', domain="[('share', '=', True)]")
    account_ids = fields.Many2many('cs.account', 'cs_portfolio_share_account_rel', 'share_id', 'account_id', string='Included Customer Accounts', check_company=True)
    line_ids = fields.One2many('cs.portfolio.share.line', 'share_id', string='Published Rows', copy=False)
    last_accessed_on = fields.Datetime(readonly=True, copy=False)
    access_count = fields.Integer(readonly=True, copy=False)
    portal_url = fields.Char(compute='_compute_portal_url')
    sharing_approved = fields.Boolean(
        string='Information Sharing Approved', readonly=True, copy=False)
    sharing_approved_by_id = fields.Many2one(
        'res.users', string='Approved By', readonly=True, copy=False)
    sharing_approved_on = fields.Datetime(
        string='Approved On', readonly=True, copy=False)

    @api.constrains('portal_user_ids')
    def _check_portal_users(self):
        if self.mapped('portal_user_ids').filtered(lambda user: not user.share):
            raise UserError(_('Only portal users can be authorized for an external portfolio share.'))

    def write(self, vals):
        approval_sensitive = {'account_ids', 'line_ids', 'allow_export'}
        if approval_sensitive.intersection(vals) and not {
                'sharing_approved', 'sharing_approved_by_id', 'sharing_approved_on'}.intersection(vals):
            vals = dict(vals, sharing_approved=False,
                        sharing_approved_by_id=False, sharing_approved_on=False,
                        active=False)
        return super().write(vals)

    def _compute_portal_url(self):
        base = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        for share in self:
            share.portal_url = '%s/era/portfolio/%s?access_token=%s' % (base, share.id, share.access_token or '')

    def action_prepare_snapshot(self):
        for share in self:
            share._revoke_sharing_approval()
            share.line_ids.unlink()
            for account in share.account_ids:
                adoption = self.env['cs.adoption.assessment'].search([('cs_account_id', '=', account.id), ('state', '=', 'confirmed')], order='assessment_date desc, id desc', limit=1)
                subscription = self.env['sale.order'].sudo().search([('partner_id.commercial_partner_id', '=', account.partner_id.id), ('is_subscription', '=', True)], order='next_invoice_date asc, id desc', limit=1)
                plan = ''
                for field_name in ('plan_id', 'recurring_plan_id'):
                    if field_name in subscription._fields and subscription[field_name]:
                        plan = subscription[field_name].display_name
                        break
                self.env['cs.portfolio.share.line'].create({
                    'share_id': share.id, 'customer_name': account.partner_id.name,
                    'era_csm': account.csm_user_id.name or '',
                    'era_csm_phone': account.csm_user_id.phone or '',
                    'era_csm_email': account.csm_user_id.email or '',
                    'date_of_join': account.onboarding_start_date,
                    'next_invoice_date': account.renewal_date,
                    'recurring_plan': plan,
                    'industry': account.partner_id.industry_id.name or '',
                    'active_users': adoption.active_users_30d if adoption and adoption.active_users_30d else 0,
                    'stage': account.lifecycle_stage_id.name or '',
                    'adoption': account.latest_adoption_score if account.latest_adoption_date else 0,
                    'client_website': account.partner_id.website or '',
                })
        return True

    def action_approve_sharing(self):
        for share in self:
            if not share.line_ids:
                raise UserError(_('Prepare and review the published rows before approving information sharing.'))
            share.write({
                'sharing_approved': True,
                'sharing_approved_by_id': self.env.user.id,
                'sharing_approved_on': fields.Datetime.now(),
            })

    def action_revoke_sharing_approval(self):
        self._revoke_sharing_approval()
        self.write({'active': False})

    def _revoke_sharing_approval(self):
        self.write({
            'sharing_approved': False,
            'sharing_approved_by_id': False,
            'sharing_approved_on': False,
        })

    def action_activate(self):
        for share in self:
            if not share.line_ids:
                raise UserError(_('Prepare and review the published rows before activating the share.'))
            if not share.sharing_approved:
                raise UserError(_('Approve information sharing before activating the portal or Excel export.'))
            share.active = True

    def action_revoke(self):
        self.write({'active': False})

    def action_regenerate_token(self):
        self.write({'access_token': secrets.token_urlsafe(32), 'active': False})

    def action_open_portal(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_url', 'url': self.portal_url, 'target': 'new'}


class CsPortfolioShareLine(models.Model):
    _name = 'cs.portfolio.share.line'
    _description = 'Published Customer Portfolio Row'
    _order = 'customer_name, id'

    share_id = fields.Many2one('cs.portfolio.share', required=True, ondelete='cascade')
    customer_name = fields.Char(required=True)
    era_csm = fields.Char()
    era_csm_phone = fields.Char()
    era_csm_email = fields.Char()
    date_of_join = fields.Date()
    next_invoice_date = fields.Date()
    recurring_plan = fields.Char()
    industry = fields.Char()
    number_of_employees = fields.Integer()
    number_of_users = fields.Integer()
    active_users = fields.Integer()
    stage = fields.Char()
    version = fields.Char()
    adoption = fields.Float()
    client_website = fields.Char()
    active_implemented_modules = fields.Text()
    potential_expansion = fields.Text()
    next_action = fields.Text()
    extra_notes = fields.Text()
    expansion_status = fields.Selection([('no_potential', 'No potential'), ('to_review', 'To review'), ('qualified', 'Qualified'), ('in_progress', 'In progress')])

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        lines.mapped('share_id')._revoke_sharing_approval()
        lines.mapped('share_id').write({'active': False})
        return lines

    def write(self, vals):
        result = super().write(vals)
        self.mapped('share_id')._revoke_sharing_approval()
        self.mapped('share_id').write({'active': False})
        return result

    def unlink(self):
        shares = self.mapped('share_id')
        result = super().unlink()
        shares._revoke_sharing_approval()
        shares.write({'active': False})
        return result

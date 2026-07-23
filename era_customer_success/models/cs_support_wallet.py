# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import ValidationError
from odoo.tools import SQL, drop_view_if_exists


class CsSupportWallet(models.Model):
    _name = 'cs.support.wallet'
    _description = 'Customer Support Hours Wallet'
    _auto = False
    _rec_name = 'product_id'
    _order = 'order_date desc, attention_rank desc, id desc'

    sale_line_id = fields.Many2one('sale.order.line', readonly=True)
    order_id = fields.Many2one('sale.order', readonly=True)
    cs_account_id = fields.Many2one('cs.account', readonly=True, index=True)
    partner_id = fields.Many2one('res.partner', string='Customer', readonly=True)
    csm_user_id = fields.Many2one('res.users', string='CSM Engineer', readonly=True, index=True)
    company_id = fields.Many2one('res.company', readonly=True, index=True)
    product_id = fields.Many2one('product.product', string='Support Package', readonly=True)
    purchased_hours = fields.Float(string='Purchased Hours', readonly=True)
    used_hours = fields.Float(string='Used Hours', readonly=True)
    remaining_hours = fields.Float(string='Remaining Hours', readonly=True)
    remaining_percentage = fields.Float(string='Remaining %', readonly=True, aggregator='avg')
    order_date = fields.Date(string='Purchased On', readonly=True)
    last_usage_date = fields.Date(string='Last Used On', readonly=True)
    expiry_date = fields.Date(string='Expires On', readonly=True)
    status = fields.Selection([
        ('healthy', 'Healthy'),
        ('expiring', 'Expiring Soon'),
        ('low', 'Low Balance'),
        ('critical', 'Critical Balance'),
        ('exhausted', 'Exhausted'),
        ('expired', 'Expired'),
    ], readonly=True)
    attention_rank = fields.Integer(readonly=True)

    def init(self):
        drop_view_if_exists(self.env.cr, self._table)
        hour = self.env.ref('uom.product_uom_hour')
        self.env.cr.execute(SQL("""
            CREATE VIEW %s AS (
                WITH wallet_base AS (
                    SELECT
                        sol.id,
                        sol.id AS sale_line_id,
                        sol.order_id,
                        acc.id AS cs_account_id,
                        so.partner_id,
                        acc.csm_user_id,
                        so.company_id,
                        sol.product_id,
                        (sol.product_uom_qty * line_uom.factor / hour_uom.factor) AS purchased_hours,
                        GREATEST(
                            (sol.product_uom_qty * line_uom.factor / hour_uom.factor)
                            - COALESCE(sol.remaining_hours, 0.0),
                            0.0
                        ) AS used_hours,
                        COALESCE(sol.remaining_hours, 0.0) AS remaining_hours,
                        CASE
                            WHEN (sol.product_uom_qty * line_uom.factor / hour_uom.factor) > 0
                            THEN 100.0 * COALESCE(sol.remaining_hours, 0.0)
                                / (sol.product_uom_qty * line_uom.factor / hour_uom.factor)
                            ELSE 0.0
                        END AS remaining_percentage,
                        so.date_order::date AS order_date,
                        timesheet_usage.last_usage_date,
                        so.date_order::date + COALESCE(company.cs_support_validity_days, 365) AS expiry_date,
                        COALESCE(company.cs_support_low_threshold, 25.0) AS low_threshold,
                        COALESCE(company.cs_support_critical_threshold, 10.0) AS critical_threshold,
                        COALESCE(company.cs_support_expiry_warning_days, 30) AS expiry_warning_days
                    FROM sale_order_line sol
                    JOIN sale_order so ON so.id = sol.order_id
                    JOIN res_company company ON company.id = so.company_id
                    JOIN product_product product ON product.id = sol.product_id
                    JOIN product_template template ON template.id = product.product_tmpl_id
                    JOIN uom_uom line_uom ON line_uom.id = sol.product_uom_id
                    JOIN uom_uom hour_uom ON hour_uom.id = %s
                    LEFT JOIN LATERAL (
                        SELECT MAX(timesheet.date) AS last_usage_date
                        FROM account_analytic_line timesheet
                        WHERE timesheet.so_line = sol.id
                          AND timesheet.project_id IS NOT NULL
                    ) timesheet_usage ON TRUE
                    JOIN res_partner customer ON customer.id = so.partner_id
                    JOIN cs_account acc
                      ON acc.partner_id = customer.commercial_partner_id
                     AND acc.company_id = so.company_id
                    WHERE so.state IN ('sale', 'done')
                      AND sol.display_type IS NULL
                      AND template.invoice_policy = 'order'
                      AND template.service_type = 'timesheet'
                      AND line_uom.parent_path LIKE hour_uom.parent_path || '%%'
                      AND sol.remaining_hours IS NOT NULL
                      AND (
                          EXISTS (
                              SELECT 1
                              FROM res_company_cs_support_product_rel configured
                              WHERE configured.company_id = so.company_id
                                AND configured.product_tmpl_id = product.product_tmpl_id
                          )
                          OR EXISTS (
                              SELECT 1
                              FROM helpdesk_ticket ticket
                              WHERE ticket.sale_line_id = sol.id
                          )
                      )
                )
                SELECT
                    wallet_base.*,
                    CASE
                        WHEN remaining_hours <= 0 THEN 'exhausted'
                        WHEN expiry_date < CURRENT_DATE THEN 'expired'
                        WHEN remaining_percentage <= critical_threshold THEN 'critical'
                        WHEN remaining_percentage <= low_threshold THEN 'low'
                        WHEN expiry_date <= CURRENT_DATE + expiry_warning_days THEN 'expiring'
                        ELSE 'healthy'
                    END AS status,
                    CASE
                        WHEN remaining_hours <= 0
                         AND COALESCE(last_usage_date, order_date) >= CURRENT_DATE - 90 THEN 100
                        WHEN expiry_date < CURRENT_DATE
                         AND expiry_date >= CURRENT_DATE - 90 THEN 90
                        WHEN remaining_percentage <= critical_threshold THEN 80
                        WHEN remaining_percentage <= low_threshold THEN 60
                        WHEN expiry_date <= CURRENT_DATE + expiry_warning_days THEN 40
                        ELSE 0
                    END AS attention_rank
                FROM wallet_base
            )
        """, SQL.identifier(self._table), hour.id))

    def action_open_sale_order(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.order_id.display_name,
            'res_model': 'sale.order',
            'res_id': self.order_id.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_explore_support_need(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Explore Additional Support Need'),
            'res_model': 'csm.offering',
            'view_mode': 'form',
            'target': 'current',
            'context': {
                'default_cs_account_id': self.cs_account_id.id,
                'default_partner_id': self.cs_account_id.partner_id.id,
                'default_csm_user_id': self.csm_user_id.id,
                'default_name': _('Additional Support Hours - %s', self.product_id.display_name),
                'default_need_type': 'support_hours',
                'default_support_sale_line_id': self.sale_line_id.id,
                'default_notes': _(
                    'Current package: %(package)s\nPurchased: %(purchased).1f h\n'
                    'Used: %(used).1f h\nRemaining: %(remaining).1f h\n'
                    'Status: %(status)s\n\nConfirm the customer\'s upcoming support needs before qualification.',
                    package=self.product_id.display_name,
                    purchased=self.purchased_hours,
                    used=self.used_hours,
                    remaining=self.remaining_hours,
                    status=self.status,
                ),
            },
        }


class CsmOffering(models.Model):
    _inherit = 'csm.offering'

    need_type = fields.Selection([
        ('service', 'Service'),
        ('module', 'ERA Module'),
        ('support_hours', 'Support Hours'),
    ], string='Need Type', default='service', required=True, tracking=True)
    support_sale_line_id = fields.Many2one(
        'sale.order.line', string='Current Support Package', readonly=True,
        copy=False, check_company=True)

    @api.constrains('support_sale_line_id', 'cs_account_id', 'company_id')
    def _check_support_package_customer(self):
        for offering in self.filtered('support_sale_line_id'):
            sale_line = offering.support_sale_line_id
            same_company = sale_line.company_id == offering.company_id
            same_customer = (
                sale_line.order_partner_id.commercial_partner_id
                == offering.cs_account_id.partner_id.commercial_partner_id)
            same_offering_customer = (
                offering.partner_id.commercial_partner_id
                == offering.cs_account_id.partner_id.commercial_partner_id)
            if not same_company or not same_customer or not same_offering_customer:
                raise ValidationError(_(
                    'The support package must belong to the same customer and company as the offering.'))

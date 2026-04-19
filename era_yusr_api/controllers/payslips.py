# -*- coding: utf-8 -*-
"""
Payslip endpoints:
  GET /api/yusr/payslips
  GET /api/yusr/payslips/<id>
  GET /api/yusr/payslips/<id>/pdf

Note: hr_payroll is Enterprise. We use sudo().search with model name string
so this module does not hard-depend on hr_payroll being installed.
"""
import base64

from odoo import http
from odoo.http import request, Response

from .base import ok, err, yusr_authenticated


class YusrPayslipsController(http.Controller):

    @http.route(
        '/api/yusr/payslips',
        type='http', auth='public', methods=['GET'], csrf=False, cors='*'
    )
    @yusr_authenticated
    def list_payslips(self, employee=None, **kwargs):
        if 'hr.payslip' not in request.env:
            return ok({'payslips': [], 'note': 'Payroll module not installed.'})

        payslips = request.env['hr.payslip'].sudo().search([
            ('employee_id', '=', employee.id),
            ('state', 'in', ('done', 'paid')),
        ], order='date_from desc', limit=60)

        return ok({
            'payslips': [{
                'id': p.id,
                'number': p.number,
                'period_from': p.date_from,
                'period_to': p.date_to,
                'net_amount': p.net_wage if hasattr(p, 'net_wage') else 0,
                'state': p.state,
            } for p in payslips]
        })

    @http.route(
        '/api/yusr/payslips/<int:payslip_id>',
        type='http', auth='public', methods=['GET'], csrf=False, cors='*'
    )
    @yusr_authenticated
    def get_payslip(self, payslip_id, employee=None, **kwargs):
        if 'hr.payslip' not in request.env:
            return err("Payroll module not installed.", status=404)

        p = request.env['hr.payslip'].sudo().browse(payslip_id).exists()
        if not p or p.employee_id.id != employee.id:
            return err("Payslip not found.", status=404)

        lines = [{
            'code': line.code,
            'name': line.name,
            'category': line.category_id.code if line.category_id else None,
            'amount': line.total,
        } for line in p.line_ids]

        return ok({
            'id': p.id,
            'number': p.number,
            'period_from': p.date_from,
            'period_to': p.date_to,
            'state': p.state,
            'net_amount': p.net_wage if hasattr(p, 'net_wage') else 0,
            'lines': lines,
        })

    @http.route(
        '/api/yusr/payslips/<int:payslip_id>/pdf',
        type='http', auth='public', methods=['GET'], csrf=False, cors='*'
    )
    @yusr_authenticated
    def payslip_pdf(self, payslip_id, employee=None, **kwargs):
        if 'hr.payslip' not in request.env:
            return err("Payroll module not installed.", status=404)

        p = request.env['hr.payslip'].sudo().browse(payslip_id).exists()
        if not p or p.employee_id.id != employee.id:
            return err("Payslip not found.", status=404)

        # Render PDF report. Adjust report xml id if your installation differs.
        try:
            report_ref = 'hr_payroll.action_report_payslip'
            pdf_content, _ = request.env['ir.actions.report'].sudo()._render_qweb_pdf(
                report_ref, res_ids=[p.id]
            )
        except Exception as e:
            return err(f"Failed to render PDF: {e}", status=500)

        return Response(
            pdf_content,
            status=200,
            headers=[
                ('Content-Type', 'application/pdf'),
                ('Content-Disposition', f'inline; filename="payslip-{p.number}.pdf"'),
            ],
        )

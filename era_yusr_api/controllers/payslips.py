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
        type='http', auth='none', methods=['GET'], csrf=False
    )
    @yusr_authenticated
    def list_payslips(self, employee=None, **kwargs):
        if 'hr.payslip' not in request.env:
            return ok({'payslips': [], 'note': 'Payroll module not installed.'})

        # States shown:
        #   'done'   — finalized (both Enterprise + community)
        #   'paid'   — Enterprise only; safely no-ops on community
        #   'verify' — community's "Waiting"; equivalent to a payslip
        #              HR has computed but not yet finalized. Show it
        #              so employees see in-progress months. Omit
        #              'draft' (work-in-progress numbers that can
        #              change) and 'cancel' (rejected).
        payslips = request.env['hr.payslip'].sudo().search([
            ('employee_id', '=', employee.id),
            ('state', 'in', ('done', 'paid', 'verify')),
        ], order='date_from desc', limit=60)

        # List response carries full line data so the mobile can render
        # earnings/deductions/contributions without a per-payslip detail
        # round-trip. Payslip line counts are small (5–20 rows each),
        # so this is cheaper than the N+1 alternative.
        return ok({
            'payslips': [_serialize_payslip(p, with_lines=True) for p in payslips]
        })

    @http.route(
        '/api/yusr/payslips/<int:payslip_id>',
        type='http', auth='none', methods=['GET'], csrf=False
    )
    @yusr_authenticated
    def get_payslip(self, payslip_id, employee=None, **kwargs):
        if 'hr.payslip' not in request.env:
            return err("Payroll module not installed.", status=404)

        p = request.env['hr.payslip'].sudo().browse(payslip_id).exists()
        if not p or p.employee_id.id != employee.id:
            return err("Payslip not found.", status=404)

        return ok(_serialize_payslip(p, with_lines=True))

    @http.route(
        '/api/yusr/payslips/<int:payslip_id>/pdf',
        type='http', auth='none', methods=['GET'], csrf=False
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
                ('Access-Control-Allow-Origin', '*'),
                ('Access-Control-Expose-Headers', 'Content-Disposition'),
                ('Vary', 'Origin'),
            ],
        )


def _serialize_payslip(p, with_lines=False):
    """Shared serializer. `with_lines=True` appends the line items so
    the mobile can render earnings/deductions/contributions without a
    second request.

    Written to work with BOTH Enterprise `hr_payroll` and the
    third-party `hr_payroll_community` module (Cybrosys/OpenHRMS).
    Neither field names nor states are fully compatible between them:

      - `currency_id` on hr.payslip: Enterprise yes, community no
        → fall back to company currency.
      - `net_wage`: Enterprise yes, community no → derive from the
        line tagged with code 'NET' (salary-rule convention shared
        by both).
      - states: Enterprise has 'draft/verify/done/paid/cancel',
        community omits 'paid'. Filtering on ('done', 'paid')
        still works for both.
    """
    # Currency: prefer the payslip's own when it exists, else company.
    currency_rec = getattr(p, 'currency_id', False) or (
        p.company_id.currency_id if p.company_id else False
    )
    currency = currency_rec.name if currency_rec else ''

    # Net amount: Enterprise exposes .net_wage; community doesn't.
    # Salary-rule convention is a line with code 'NET' on both.
    net_amount = getattr(p, 'net_wage', None)
    if net_amount is None:
        net_line = p.line_ids.filtered(lambda l: (l.code or '').upper() == 'NET')
        net_amount = net_line[:1].total if net_line else 0

    out = {
        'id': p.id,
        'number': p.number,
        'period_from': p.date_from,
        'period_to': p.date_to,
        'net_amount': net_amount,
        'currency': currency,
        'state': p.state,
    }
    if with_lines:
        out['lines'] = [{
            'code': line.code,
            # hr.payslip.line.name is a plain Char stored at generation
            # time in whichever language the payroll clerk had active,
            # so it doesn't react to env.context['lang']. The rule name
            # on hr.salary.rule IS translate=True — read from the rule
            # so Arabic clients see Arabic labels. Fallback to the
            # stored line name if the rule is missing (edge case).
            'name': (line.salary_rule_id.name if line.salary_rule_id else None) or line.name,
            'category': line.category_id.code if line.category_id else None,
            'amount': line.total,
        } for line in p.line_ids]
    return out

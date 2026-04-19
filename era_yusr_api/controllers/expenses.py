# -*- coding: utf-8 -*-
"""
Expense endpoints:
  GET  /api/yusr/expenses
  POST /api/yusr/expenses
  GET  /api/yusr/expenses/categories
"""
import base64

from odoo import http, fields
from odoo.http import request

from .base import ok, err, get_json_payload, yusr_authenticated


class YusrExpensesController(http.Controller):

    @http.route(
        '/api/yusr/expenses',
        type='http', auth='public', methods=['GET'], csrf=False, cors='*'
    )
    @yusr_authenticated
    def list_expenses(self, employee=None, **kwargs):
        expenses = request.env['hr.expense'].sudo().search([
            ('employee_id', '=', employee.id),
        ], order='date desc, id desc', limit=100)

        return ok({
            'expenses': [{
                'id': e.id,
                'name': e.name,
                'date': e.date,
                'amount': e.total_amount,
                'currency': e.currency_id.name,
                'category': e.product_id.name if e.product_id else None,
                'state': e.state,
                'has_attachment': bool(e.message_main_attachment_id),
            } for e in expenses]
        })

    @http.route(
        '/api/yusr/expenses/categories',
        type='http', auth='public', methods=['GET'], csrf=False, cors='*'
    )
    @yusr_authenticated
    def categories(self, employee=None, **kwargs):
        products = request.env['product.product'].sudo().search([
            ('can_be_expensed', '=', True),
        ])
        return ok({
            'categories': [{
                'id': p.id, 'name': p.name,
            } for p in products]
        })

    @http.route(
        '/api/yusr/expenses',
        type='http', auth='public', methods=['POST'], csrf=False, cors='*'
    )
    @yusr_authenticated
    def create_expense(self, employee=None, **kwargs):
        payload = get_json_payload()
        required = ['name', 'product_id', 'total_amount']
        for f in required:
            if not payload.get(f):
                return err(f"Field '{f}' is required.", status=400)

        vals = {
            'employee_id': employee.id,
            'name': payload['name'],
            'product_id': int(payload['product_id']),
            'total_amount': float(payload['total_amount']),
            'date': payload.get('date') or fields.Date.today(),
            'description': payload.get('description', ''),
        }
        if payload.get('currency_id'):
            vals['currency_id'] = int(payload['currency_id'])

        expense = request.env['hr.expense'].sudo().create(vals)

        # Attach receipt if provided as base64
        attachment_b64 = payload.get('receipt_base64')
        attachment_name = payload.get('receipt_filename', 'receipt.jpg')
        if attachment_b64:
            try:
                request.env['ir.attachment'].sudo().create({
                    'name': attachment_name,
                    'datas': attachment_b64,
                    'res_model': 'hr.expense',
                    'res_id': expense.id,
                })
            except Exception:
                pass  # Don't fail the whole request for attachment issues

        return ok({
            'expense_id': expense.id,
            'state': expense.state,
            'message': 'Expense submitted.',
        }, status=201)

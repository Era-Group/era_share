# -*- coding: utf-8 -*-
"""
Leaves endpoints:
  GET  /api/yusr/leaves/balances
  GET  /api/yusr/leaves/requests
  POST /api/yusr/leaves/requests
  POST /api/yusr/leaves/requests/<id>/cancel
"""
from datetime import datetime, date

from odoo import http, fields
from odoo.http import request

from .base import ok, err, get_json_payload, yusr_authenticated


class YusrLeavesController(http.Controller):

    @http.route(
        '/api/yusr/leaves/balances',
        type='http', auth='public', methods=['GET'], csrf=False, cors='*'
    )
    @yusr_authenticated
    def balances(self, employee=None, **kwargs):
        """Return leave balances grouped by leave type."""
        types = request.env['hr.leave.type'].sudo().search([
            ('active', '=', True),
            ('requires_allocation', '=', 'yes'),
        ])
        data = []
        for lt in types:
            # Use Odoo's built-in balance computation
            balance = lt.with_context(
                employee_id=employee.id
            ).get_allocation_data([employee])[employee][lt.name][1] if False else None

            # Simpler direct computation via allocations:
            allocations = request.env['hr.leave.allocation'].sudo().search([
                ('employee_id', '=', employee.id),
                ('holiday_status_id', '=', lt.id),
                ('state', '=', 'validate'),
            ])
            total_allocated = sum(a.number_of_days for a in allocations)
            taken = sum(
                lr.number_of_days for lr in request.env['hr.leave'].sudo().search([
                    ('employee_id', '=', employee.id),
                    ('holiday_status_id', '=', lt.id),
                    ('state', '=', 'validate'),
                ])
            )
            remaining = total_allocated - taken

            data.append({
                'id': lt.id,
                'name': lt.name,
                'color': lt.color,
                'allocated': total_allocated,
                'taken': taken,
                'remaining': remaining,
                'unit': 'days',
            })
        return ok({'balances': data})

    @http.route(
        '/api/yusr/leaves/types',
        type='http', auth='public', methods=['GET'], csrf=False, cors='*'
    )
    @yusr_authenticated
    def leave_types(self, employee=None, **kwargs):
        """List available leave types for requesting."""
        types = request.env['hr.leave.type'].sudo().search([('active', '=', True)])
        return ok({
            'types': [{
                'id': t.id, 'name': t.name, 'requires_allocation': t.requires_allocation,
                'color': t.color,
            } for t in types]
        })

    @http.route(
        '/api/yusr/leaves/requests',
        type='http', auth='public', methods=['GET'], csrf=False, cors='*'
    )
    @yusr_authenticated
    def list_requests(self, employee=None, **kwargs):
        leaves = request.env['hr.leave'].sudo().search(
            [('employee_id', '=', employee.id)],
            order='create_date desc', limit=100,
        )
        return ok({'requests': [_serialize_leave(l) for l in leaves]})

    @http.route(
        '/api/yusr/leaves/requests',
        type='http', auth='public', methods=['POST'], csrf=False, cors='*'
    )
    @yusr_authenticated
    def create_request(self, employee=None, **kwargs):
        payload = get_json_payload()
        required = ['holiday_status_id', 'date_from', 'date_to']
        for f in required:
            if not payload.get(f):
                return err(f"Field '{f}' is required.", status=400)

        try:
            date_from = datetime.fromisoformat(payload['date_from'])
            date_to = datetime.fromisoformat(payload['date_to'])
        except ValueError:
            return err("Invalid date format. Use ISO 8601.", status=400)

        vals = {
            'employee_id': employee.id,
            'holiday_status_id': int(payload['holiday_status_id']),
            'request_date_from': date_from.date(),
            'request_date_to': date_to.date(),
            'name': payload.get('reason') or 'Leave request (Yusr)',
        }
        if payload.get('half_day'):
            vals['request_unit_half'] = True

        leave = request.env['hr.leave'].sudo().create(vals)
        # Move to confirm state
        leave.action_confirm()

        return ok({
            'request_id': leave.id,
            'state': leave.state,
            'message': 'Leave request submitted.',
        }, status=201)

    @http.route(
        '/api/yusr/leaves/requests/<int:leave_id>/cancel',
        type='http', auth='public', methods=['POST'], csrf=False, cors='*'
    )
    @yusr_authenticated
    def cancel_request(self, leave_id, employee=None, **kwargs):
        leave = request.env['hr.leave'].sudo().browse(leave_id).exists()
        if not leave or leave.employee_id.id != employee.id:
            return err("Request not found.", status=404)
        if leave.state not in ('draft', 'confirm'):
            return err("Only pending requests can be cancelled.", status=400)
        leave.action_refuse()  # or action_draft then unlink
        return ok({'message': 'Request cancelled.'})


def _serialize_leave(l):
    return {
        'id': l.id,
        'name': l.name,
        'type': l.holiday_status_id.name,
        'type_id': l.holiday_status_id.id,
        'date_from': l.request_date_from,
        'date_to': l.request_date_to,
        'number_of_days': l.number_of_days,
        'state': l.state,
        'state_label': dict(l._fields['state'].selection).get(l.state),
        'create_date': l.create_date,
    }

# -*- coding: utf-8 -*-
"""
Push notification device endpoints:
  POST   /api/yusr/device/register    - Register a push token
  DELETE /api/yusr/device/unregister  - Remove a push token (on logout)
"""
from odoo import http, fields
from odoo.http import request

from .base import ok, err, get_json_payload, yusr_authenticated


class YusrDeviceController(http.Controller):

    @http.route(
        '/api/yusr/device/register',
        type='http', auth='none', methods=['POST'], csrf=False
    )
    @yusr_authenticated
    def register(self, employee=None, **kwargs):
        payload = get_json_payload()
        token = (payload.get('device_token') or '').strip()
        platform = payload.get('platform')
        app_version = payload.get('app_version', '')

        if not token:
            return err("device_token is required.", status=400)
        if platform not in ('ios', 'android'):
            return err("platform must be 'ios' or 'android'.", status=400)

        Device = request.env['yusr.device'].sudo()
        existing = Device.search([('device_token', '=', token)], limit=1)
        if existing:
            existing.write({
                'employee_id': employee.id,
                'platform': platform,
                'app_version': app_version,
                'last_seen': fields.Datetime.now(),
                'active': True,
            })
            return ok({'device_id': existing.id, 'message': 'Device updated.'})

        device = Device.create({
            'employee_id': employee.id,
            'device_token': token,
            'platform': platform,
            'app_version': app_version,
        })
        return ok({'device_id': device.id, 'message': 'Device registered.'}, status=201)

    @http.route(
        '/api/yusr/device/unregister',
        type='http', auth='none', methods=['DELETE', 'POST'], csrf=False
    )
    @yusr_authenticated
    def unregister(self, employee=None, **kwargs):
        payload = get_json_payload()
        token = (payload.get('device_token') or '').strip()
        if not token:
            return err("device_token is required.", status=400)

        device = request.env['yusr.device'].sudo().search([
            ('device_token', '=', token),
            ('employee_id', '=', employee.id),
        ], limit=1)
        if device:
            device.active = False
        return ok({'message': 'Device unregistered.'})

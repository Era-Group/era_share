# -*- coding: utf-8 -*-
"""
HR requests endpoints (uses mail.thread on hr.employee for now):
  GET  /api/yusr/hr/requests
  POST /api/yusr/hr/requests
  POST /api/yusr/hr/requests/<id>/message

For a richer implementation, create a dedicated `yusr.hr.request` model
with state, category, and attachments. Kept simple here.
"""
from odoo import http
from odoo.http import request

from .base import ok, err, get_json_payload, yusr_authenticated


class YusrHRRequestsController(http.Controller):

    @http.route(
        '/api/yusr/hr/requests',
        type='http', auth='none', methods=['GET'], csrf=False
    )
    @yusr_authenticated
    def list_requests(self, employee=None, **kwargs):
        # Return recent messages posted on the employee record
        messages = request.env['mail.message'].sudo().search([
            ('model', '=', 'hr.employee'),
            ('res_id', '=', employee.id),
            ('message_type', 'in', ('comment', 'email')),
        ], order='date desc', limit=50)

        return ok({
            'messages': [{
                'id': m.id,
                'author': m.author_id.name if m.author_id else 'System',
                'is_self': m.author_id.id == (employee.user_id.partner_id.id if employee.user_id else 0),
                'body': m.body,
                'date': m.date,
                'subject': m.subject,
            } for m in messages]
        })

    @http.route(
        '/api/yusr/hr/requests',
        type='http', auth='none', methods=['POST'], csrf=False
    )
    @yusr_authenticated
    def create_request(self, employee=None, **kwargs):
        payload = get_json_payload()
        subject = payload.get('subject', '').strip()
        body = payload.get('body', '').strip()
        category = payload.get('category', 'HR')

        if not body:
            return err("Message body is required.", status=400)

        full_body = f"<p><b>[{category}]</b></p><p>{body}</p>"
        msg = employee.sudo().message_post(
            body=full_body,
            subject=subject or f"{category} Request from {employee.name}",
            message_type='comment',
            subtype_xmlid='mail.mt_comment',
        )

        # Notify HR group
        hr_users = request.env.ref('hr.group_hr_user').users
        for hr_user in hr_users[:5]:  # cap to avoid spam
            employee.sudo().activity_schedule(
                'mail.mail_activity_data_todo',
                summary=f"Yusr request from {employee.name}: {subject or category}",
                user_id=hr_user.id,
            )

        return ok({
            'message_id': msg.id,
            'message': 'Request submitted.',
        }, status=201)

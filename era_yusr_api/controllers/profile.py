# -*- coding: utf-8 -*-
"""
Profile endpoints:
  GET  /api/yusr/profile   - Get current employee profile
  PUT  /api/yusr/profile   - Request profile update (creates change request for HR approval)
"""
from odoo import http
from odoo.http import request
from .base import ok, err, get_json_payload, yusr_authenticated


class YusrProfileController(http.Controller):

    @http.route(
        '/api/yusr/profile',
        type='http', auth='public', methods=['GET'], csrf=False, cors='*'
    )
    @yusr_authenticated
    def get_profile(self, employee=None, **kwargs):
        return ok(_serialize_full_profile(employee))

    @http.route(
        '/api/yusr/profile',
        type='http', auth='public', methods=['PUT'], csrf=False, cors='*'
    )
    @yusr_authenticated
    def update_profile(self, employee=None, **kwargs):
        """
        Employees can't directly edit protected fields. We log the request
        as a chatter message to hr.employee. HR reviews and applies changes.
        """
        payload = get_json_payload()
        allowed_request_fields = [
            'mobile_phone', 'work_phone', 'private_phone',
            'emergency_contact', 'emergency_phone',
            'private_street', 'private_city', 'private_zip',
        ]
        requested = {k: v for k, v in payload.items() if k in allowed_request_fields}
        if not requested:
            return err("No valid fields to update.", status=400)

        body = "Profile update request from Yusr mobile app:<ul>"
        for k, v in requested.items():
            body += f"<li><b>{k}</b>: {v}</li>"
        body += "</ul>"
        employee.sudo().message_post(
            body=body,
            subject="Profile Update Request",
            message_type='comment',
        )
        return ok({'message': 'Update request submitted for HR approval.'})


def _serialize_full_profile(emp):
    return {
        'id': emp.id,
        'name': emp.name,
        'login_id': emp.employee_login_id,
        'job_title': emp.job_title,
        'department': emp.department_id.name if emp.department_id else None,
        'manager': emp.parent_id.name if emp.parent_id else None,
        'company': emp.company_id.name if emp.company_id else None,
        'work_email': emp.work_email,
        'work_phone': emp.work_phone,
        'mobile_phone': emp.mobile_phone,
        'contract_start_date': emp.first_contract_date if 'first_contract_date' in emp._fields else None,
        'identification_id': emp.identification_id,
        'avatar_url': f"/web/image/hr.employee/{emp.id}/image_256",
    }

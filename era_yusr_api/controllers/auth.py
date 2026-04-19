# -*- coding: utf-8 -*-
"""
Authentication endpoints:
  POST /api/yusr/auth/login        - Login with Employee ID + PIN
  POST /api/yusr/auth/refresh      - Refresh access token
  POST /api/yusr/auth/logout       - Invalidate current session (client-side)
  POST /api/yusr/auth/forgot-pin   - Send PIN reset request to HR
"""
import logging

from odoo import http
from odoo.http import request
from odoo.exceptions import AccessDenied, ValidationError

from .base import ok, err, get_json_payload, yusr_authenticated
from ..utils.jwt_helper import generate_tokens, decode_token

_logger = logging.getLogger(__name__)


class YusrAuthController(http.Controller):

    @http.route(
        '/api/yusr/auth/login',
        type='http', auth='public', methods=['POST'], csrf=False, cors='*'
    )
    def login(self, **kwargs):
        try:
            payload = get_json_payload()
            login_id = (payload.get('employee_id') or '').strip()
            pin = (payload.get('pin') or '').strip()

            if not login_id or not pin:
                return err("employee_id and pin are required.", status=400)

            employee = request.env['hr.employee'].sudo().authenticate_yusr(login_id, pin)
            tokens = generate_tokens(request.env, employee)

            _logger.info("Yusr login success: employee_id=%s login_id=%s",
                         employee.id, login_id)

            return ok({
                'token': tokens['access_token'],
                'refresh_token': tokens['refresh_token'],
                'expires_in': tokens['expires_in'],
                'token_type': tokens['token_type'],
                'employee': _serialize_employee_brief(employee),
            })
        except AccessDenied as e:
            return err(str(e) or "Invalid credentials.", status=401, code='INVALID_CREDENTIALS')
        except ValidationError as e:
            return err(str(e), status=400)
        except Exception:
            _logger.exception("Login error")
            return err("Internal server error.", status=500)

    @http.route(
        '/api/yusr/auth/refresh',
        type='http', auth='public', methods=['POST'], csrf=False, cors='*'
    )
    def refresh(self, **kwargs):
        try:
            payload = get_json_payload()
            refresh_token = payload.get('refresh_token')
            if not refresh_token:
                return err("refresh_token is required.", status=400)

            decoded = decode_token(request.env, refresh_token, expected_type='refresh')
            employee = request.env['hr.employee'].sudo().browse(decoded['sub']).exists()
            if not employee or not employee.active or not employee.yusr_access_enabled:
                return err("Employee not found or inactive.", status=401)

            tokens = generate_tokens(request.env, employee)
            return ok({
                'token': tokens['access_token'],
                'refresh_token': tokens['refresh_token'],
                'expires_in': tokens['expires_in'],
            })
        except AccessDenied as e:
            return err(str(e), status=401, code='INVALID_REFRESH_TOKEN')
        except Exception:
            _logger.exception("Refresh error")
            return err("Internal server error.", status=500)

    @http.route(
        '/api/yusr/auth/logout',
        type='http', auth='public', methods=['POST'], csrf=False, cors='*'
    )
    @yusr_authenticated
    def logout(self, employee=None, **kwargs):
        """
        Logout is client-side (discard token). Server-side, we only log the event.
        For true revocation, implement a token blacklist table.
        """
        _logger.info("Yusr logout: employee_id=%s", employee.id)
        return ok({'message': 'Logged out successfully.'})

    @http.route(
        '/api/yusr/auth/forgot-pin',
        type='http', auth='public', methods=['POST'], csrf=False, cors='*'
    )
    def forgot_pin(self, **kwargs):
        """Create an HR activity requesting PIN reset."""
        try:
            payload = get_json_payload()
            login_id = (payload.get('employee_id') or '').strip()
            if not login_id:
                return err("employee_id is required.", status=400)

            emp = request.env['hr.employee'].sudo().search([
                ('employee_login_id', '=ilike', login_id),
                ('active', '=', True),
            ], limit=1)

            # Always return success to avoid user enumeration
            if emp:
                emp.message_post(
                    body=f"Employee requested a PIN reset from the Yusr mobile app.",
                    subject="Yusr PIN Reset Request",
                    message_type='comment',
                )
                # Optionally notify HR group
                hr_users = request.env.ref('hr.group_hr_user').users
                for hr_user in hr_users:
                    emp.activity_schedule(
                        'mail.mail_activity_data_todo',
                        summary=f"Reset Yusr PIN for {emp.name}",
                        user_id=hr_user.id,
                    )

            return ok({'message': 'If the employee exists, HR has been notified.'})
        except Exception:
            _logger.exception("Forgot PIN error")
            return err("Internal server error.", status=500)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def _serialize_employee_brief(emp):
    return {
        'id': emp.id,
        'name': emp.name,
        'login_id': emp.employee_login_id,
        'job_title': emp.job_title,
        'department': emp.department_id.name if emp.department_id else None,
        'manager': emp.parent_id.name if emp.parent_id else None,
        'avatar_url': f"/web/image/hr.employee/{emp.id}/image_128",
        'roles': _get_roles(emp),
    }


def _get_roles(emp):
    """Return a list of role strings based on employee's linked user groups."""
    roles = ['employee']
    user = emp.user_id
    if user:
        if user.has_group('hr.group_hr_manager'):
            roles.append('hr_admin')
        elif user.has_group('hr.group_hr_user'):
            roles.append('hr_user')
        # Manager: has subordinates
        if emp.child_ids:
            roles.append('manager')
    else:
        # Even without user account, manager role if has subordinates
        if emp.child_ids:
            roles.append('manager')
    return roles

# -*- coding: utf-8 -*-
"""
JWT helper for Yusr API.
Handles encoding/decoding of access tokens and refresh tokens.
"""
import logging
from datetime import datetime, timedelta, timezone

import jwt  # PyJWT
from odoo import api, SUPERUSER_ID
from odoo.exceptions import AccessDenied

_logger = logging.getLogger(__name__)

ACCESS_TOKEN_TTL_MINUTES = 60 * 8        # 8 hours
REFRESH_TOKEN_TTL_DAYS = 30              # 30 days
ALGORITHM = 'HS256'


def _get_secret(env):
    """Fetch JWT secret from ir.config_parameter. Fail hard if not set."""
    secret = env['ir.config_parameter'].sudo().get_param('era_yusr_api.jwt_secret')
    if not secret or secret == 'CHANGE_ME_IN_PRODUCTION':
        raise AccessDenied(
            "JWT secret not configured. Set era_yusr_api.jwt_secret in system parameters."
        )
    return secret


def generate_tokens(env, employee):
    """
    Generate access + refresh tokens for an employee.
    Returns dict: { access_token, refresh_token, expires_in }
    """
    secret = _get_secret(env)
    now = datetime.now(timezone.utc)

    access_payload = {
        'sub': employee.id,
        'employee_login_id': employee.employee_login_id or '',
        'type': 'access',
        'iat': now,
        'exp': now + timedelta(minutes=ACCESS_TOKEN_TTL_MINUTES),
    }
    refresh_payload = {
        'sub': employee.id,
        'type': 'refresh',
        'iat': now,
        'exp': now + timedelta(days=REFRESH_TOKEN_TTL_DAYS),
    }

    access_token = jwt.encode(access_payload, secret, algorithm=ALGORITHM)
    refresh_token = jwt.encode(refresh_payload, secret, algorithm=ALGORITHM)

    return {
        'access_token': access_token,
        'refresh_token': refresh_token,
        'expires_in': ACCESS_TOKEN_TTL_MINUTES * 60,
        'token_type': 'Bearer',
    }


def decode_token(env, token, expected_type='access'):
    """
    Decode and validate a JWT. Returns the payload dict.
    Raises AccessDenied on any failure.
    """
    secret = _get_secret(env)
    try:
        payload = jwt.decode(token, secret, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise AccessDenied("Token has expired.")
    except jwt.InvalidTokenError as e:
        _logger.warning("Invalid JWT: %s", e)
        raise AccessDenied("Invalid token.")

    if payload.get('type') != expected_type:
        raise AccessDenied(f"Wrong token type. Expected {expected_type}.")

    return payload


def get_employee_from_token(env, token):
    """Decode access token and return the hr.employee recordset."""
    payload = decode_token(env, token, expected_type='access')
    employee = env['hr.employee'].sudo().browse(payload['sub']).exists()
    if not employee or not employee.active:
        raise AccessDenied("Employee not found or inactive.")
    return employee

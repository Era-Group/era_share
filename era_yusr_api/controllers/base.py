# -*- coding: utf-8 -*-
"""
Base controller utilities for the Yusr API.
Provides:
  - JSON request/response helpers
  - JWT authentication decorator
  - CORS-friendly error responses
"""
import functools
import json
import logging

from odoo import http
from odoo.http import request, Response
from odoo.exceptions import AccessDenied, ValidationError, UserError

from ..utils.jwt_helper import get_employee_from_token

_logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------
# Response helpers
# ----------------------------------------------------------------------
def _json_response(data, status=200):
    """Return a JSON HTTP response with correct headers."""
    return Response(
        json.dumps(data, default=str),
        status=status,
        headers=[('Content-Type', 'application/json')],
    )


def ok(data=None, **kwargs):
    body = {'success': True, 'data': data if data is not None else {}}
    body.update(kwargs)
    return _json_response(body, status=200)


def err(message, status=400, code=None, **extra):
    body = {'success': False, 'error': message}
    if code:
        body['code'] = code
    body.update(extra)
    return _json_response(body, status=status)


# ----------------------------------------------------------------------
# Request parsing
# ----------------------------------------------------------------------
def get_json_payload():
    """Parse request body as JSON. Returns {} on empty body."""
    try:
        raw = request.httprequest.data
        if not raw:
            return {}
        return json.loads(raw)
    except (ValueError, TypeError):
        raise ValidationError("Invalid JSON body.")


def get_bearer_token():
    """Extract Bearer token from Authorization header."""
    auth_header = request.httprequest.headers.get('Authorization', '')
    if not auth_header.startswith('Bearer '):
        return None
    return auth_header[7:].strip()


# ----------------------------------------------------------------------
# Auth decorator
# ----------------------------------------------------------------------
def yusr_authenticated(func):
    """
    Decorator for endpoints requiring a valid access token.
    Injects `employee` kwarg into the handler.
    """
    @functools.wraps(func)
    def wrapper(self, *args, **kwargs):
        try:
            token = get_bearer_token()
            if not token:
                return err("Missing Authorization header.", status=401, code='NO_TOKEN')
            employee = get_employee_from_token(request.env, token)
            kwargs['employee'] = employee
            return func(self, *args, **kwargs)
        except AccessDenied as e:
            return err(str(e) or "Unauthorized.", status=401, code='UNAUTHORIZED')
        except ValidationError as e:
            return err(str(e), status=400, code='VALIDATION_ERROR')
        except UserError as e:
            return err(str(e), status=400, code='USER_ERROR')
        except Exception as e:
            _logger.exception("Yusr API internal error")
            return err("Internal server error.", status=500, code='INTERNAL_ERROR')
    return wrapper


# ----------------------------------------------------------------------
# CORS preflight
# ----------------------------------------------------------------------
class YusrBaseController(http.Controller):
    """Base class; also handles OPTIONS preflight for all /api/yusr routes."""

    @http.route(
        '/api/yusr/<path:path>',
        type='http', auth='public', methods=['OPTIONS'], csrf=False, cors='*'
    )
    def preflight(self, path, **kwargs):
        return Response(
            '', status=204,
            headers=[
                ('Access-Control-Allow-Origin', '*'),
                ('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, OPTIONS'),
                ('Access-Control-Allow-Headers', 'Content-Type, Authorization'),
                ('Access-Control-Max-Age', '3600'),
            ],
        )

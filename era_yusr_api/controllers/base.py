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
from datetime import date, datetime, timezone

from odoo import http, _
from odoo.http import request, Response
from odoo.exceptions import AccessDenied, ValidationError, UserError

from ..utils.jwt_helper import get_employee_from_token

_logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------
# JSON encoding
# ----------------------------------------------------------------------
def _json_default(value):
    """json.dumps default= hook.

    Odoo stores every datetime naively, in UTC. Previously we fell back
    to str() which produced `"2026-04-19 08:15:00"` — a bare, unzoned
    string that JavaScript's `new Date(...)` parses as LOCAL time of the
    device. For a Saudi Arabia phone (UTC+3) that means every UTC
    timestamp was read 3 hours too early.

    Emit ISO 8601 with an explicit `Z` marker so the client parses as
    UTC and can format into the employee's local timezone for display.
    date (not datetime) values are emitted plain ISO — there's no TZ to
    attach, and calendar dates (e.g. leave.request_date_from) should
    never drift by local offset.

    Fallback to str() for anything else (Odoo recordsets, Decimals, etc.)
    so existing endpoints keep working without per-site changes.
    """
    # Note: datetime is a subclass of date, so check datetime first.
    if isinstance(value, datetime):
        # Treat naive as UTC (Odoo's convention). If a controller ever
        # hands us an aware datetime, honor its tzinfo by converting to
        # UTC first.
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return value.strftime('%Y-%m-%dT%H:%M:%SZ')
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


# ----------------------------------------------------------------------
# Response helpers
# ----------------------------------------------------------------------
def _json_response(data, status=200):
    """Return a JSON HTTP response with CORS headers.

    Every success AND error response carries Access-Control-Allow-Origin
    so browsers don't reject 4xx/5xx responses (which would otherwise
    surface to the client as opaque "Failed to fetch" instead of the
    actual API error message). `cors='*'` is intentionally OFF the
    individual routes — Odoo's cors decorator both auto-handles OPTIONS
    unreliably (especially on routes wrapped with @yusr_authenticated)
    and stacks a duplicate Allow-Origin on the response.
    """
    return Response(
        json.dumps(data, default=_json_default),
        status=status,
        headers=[
            ('Content-Type', 'application/json'),
            ('Access-Control-Allow-Origin', '*'),
            ('Access-Control-Expose-Headers', 'Content-Disposition'),
            ('Vary', 'Origin'),
        ],
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
    """Parse request body as JSON, regardless of Content-Type.

    Web clients sometimes send `Content-Type: text/plain;charset=UTF-8`
    on POST/PUT to keep the request CORS-"simple" (avoids preflight).
    Native clients send `application/json`. Treat both as JSON: read
    the raw body and parse. Empty body → {}.
    """
    try:
        raw = request.httprequest.get_data(as_text=True)
        if not raw:
            return {}
        return json.loads(raw)
    except (ValueError, TypeError):
        raise ValidationError("Invalid JSON body.")


def get_bearer_token():
    """Resolve the access token from header OR query string.

    1. `Authorization: Bearer <token>` header (native apps).
    2. `?access_token=<token>` query string (web fallback — some
       browsers/CDNs strip the Authorization header on cross-origin
       requests; the query-string form keeps the request CORS-"simple"
       and survives proxies).

    Returns None if neither source has a non-empty token.
    """
    auth_header = request.httprequest.headers.get('Authorization', '')
    if auth_header.startswith('Bearer '):
        token = auth_header[7:].strip()
        if token:
            return token
    qs_token = request.httprequest.args.get('access_token', '').strip()
    return qs_token or None


# ----------------------------------------------------------------------
# Language resolution
# ----------------------------------------------------------------------
def _resolve_request_lang():
    """Return an Odoo locale code ('ar_001' or 'en_US') based on the
    request's language preference.

    Order of precedence:
      1. `?lang=` query string
      2. `Accept-Language` header (first tag, e.g. `ar`, `en-US`)
      3. default `en_US`

    The mobile app sends its current i18next language ('ar'/'en') via
    Accept-Language. We map the short code to the actual installed
    Odoo locale; falling back to en_US if the Arabic variant isn't
    installed on this instance.
    """
    raw = request.httprequest.args.get('lang') or ''
    if not raw:
        accept = request.httprequest.headers.get('Accept-Language', '') or ''
        raw = accept.split(',')[0].split(';')[0].strip()
    raw = raw.lower().replace('-', '_')
    if raw.startswith('ar'):
        preferred = ['ar_001', 'ar_SA', 'ar_SY', 'ar']
    else:
        return 'en_US'
    # Pick the first installed matching Arabic variant
    Lang = request.env['res.lang'].sudo().with_context(active_test=False)
    for code in preferred:
        if Lang.search_count([('code', '=', code), ('active', '=', True)]):
            return code
    return 'en_US'


def apply_request_lang():
    """Set the request env's language context so that `_()` and any
    Odoo-raised ValidationError/UserError messages come back in the
    user's language.

    Idempotent; call once per request before any ORM operation whose
    translated output you care about.
    """
    lang = _resolve_request_lang()
    try:
        request.update_env(context=dict(request.env.context, lang=lang))
    except Exception:
        # Older Odoo builds: fall back to .with_context on a per-call basis
        _logger.debug("Could not update request env lang to %s", lang)
    return lang


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
        apply_request_lang()
        try:
            token = get_bearer_token()
            if not token:
                return err(
                    _("Missing Authorization header."),
                    status=401, code='NO_TOKEN',
                )
            employee = get_employee_from_token(request.env, token)
            kwargs['employee'] = employee
            return func(self, *args, **kwargs)
        except AccessDenied as e:
            return err(str(e) or _("Unauthorized."), status=401, code='UNAUTHORIZED')
        except ValidationError as e:
            return err(str(e), status=400, code='VALIDATION_ERROR')
        except UserError as e:
            return err(str(e), status=400, code='USER_ERROR')
        except Exception:
            _logger.exception("Yusr API internal error")
            return err(_("Internal server error."), status=500, code='INTERNAL_ERROR')
    return wrapper


# ----------------------------------------------------------------------
# CORS preflight
# ----------------------------------------------------------------------
# Browsers (and Expo Web) send an OPTIONS preflight before any request
# carrying `Authorization: Bearer …` or a non-form Content-Type. Odoo's
# `cors='*'` flag only decorates the actual response — preflight needs
# its own handler.
#
# auth='none' (NOT 'public') is critical: 'public' triggers Odoo's
# session lookup, which can fail on a cross-origin OPTIONS that has no
# session cookie, and the failure response lacks Access-Control-* —
# so the browser blocks the real request before it ever runs.
class YusrBaseController(http.Controller):
    """Handles OPTIONS preflight for every /api/yusr/* route."""

    @http.route(
        '/api/yusr/<path:path>',
        type='http', auth='none', methods=['OPTIONS'], csrf=False
    )
    def preflight(self, path, **kwargs):
        headers = [
            ('Access-Control-Allow-Origin', '*'),
            ('Access-Control-Allow-Methods',
             'GET, POST, PUT, DELETE, PATCH, OPTIONS'),
            ('Access-Control-Allow-Headers',
             'Authorization, Content-Type, Accept, X-Requested-With'),
            ('Access-Control-Expose-Headers', 'Content-Disposition'),
            ('Access-Control-Max-Age', '86400'),
            ('Vary', 'Origin'),
        ]
        return Response('', status=204, headers=headers)

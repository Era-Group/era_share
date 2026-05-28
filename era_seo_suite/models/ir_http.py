"""ERA SEO — ir.http dispatch hook for redirect resolution.

Intercepts request dispatch BEFORE the standard 404 response so admin-managed
redirects (``era.seo.redirect``) take precedence over a "page not found" page.

Hook flow on every frontend request:

  1. ``_serve_fallback`` is called by Odoo when no route or website page
     matched the path.
  2. System paths (``/web/``, ``/my/``, ``/static/``, ``/website/static/``)
     and HEAD/OPTIONS requests are skipped entirely — they're never user
     content and accidental redirects there can lock the admin out.
  3. The request path is normalized (query/fragment stripped, leading slash
     ensured) and the language prefix (e.g. ``/ar/``) is removed before
     lookup, so a rule for ``/old`` also matches ``/ar/old``.
  4. We look up a redirect rule with the request scope (website + lang).
     If a plain match misses, we retry with the trailing slash toggled so
     ``/foo`` and ``/foo/`` resolve to the same rule without forcing the
     admin to author both.
  5. On hit:
       - 410: return a 410 ``Gone`` response.
       - Others: return a Werkzeug redirect (301/302/307/308). The original
         query string is forwarded onto the target so tracking parameters
         survive the hop. Hop counter via cookie aborts runaway chains
         with 508 after ``REDIRECT_HOP_LIMIT`` bounces.
  6. On miss: log to ``era.seo.redirect.log`` (404 capture) on a dedicated
     cursor (Odoo rolls the main transaction back on 404) and fall through
     to the parent.

Coexistence with stock ``website.rewrite``:
  Odoo's ``website`` module installs its own redirect handler that fires
  earlier in dispatch (during route matching for ``website.page`` records).
  Stock redirects therefore take precedence over ERA redirects for the same
  path. In practice, choose one UI per site — mixing both is supported but
  the stock entry wins on conflict.

Per SPEC §9.2 / §9.4.
"""
import logging

import werkzeug

from odoo import models
from odoo.http import request

from .seo_redirect import REDIRECT_HOP_LIMIT

_logger = logging.getLogger(__name__)

# Cookie name used to detect redirect loops across browser-followed hops.
_HOP_COOKIE = 'era_seo_hops'
# Cookie lifetime (seconds). Just long enough to span a redirect chain; we
# don't want a stale counter to permanently 508 a user.
_HOP_COOKIE_MAX_AGE = 10

# Path prefixes the redirect hook MUST NOT touch. These are Odoo backend,
# portal, and static asset URLs; redirecting them risks locking the admin
# out of the instance.
_SYSTEM_PATH_PREFIXES = (
    '/web/',
    '/my/',
    '/odoo/',
    '/static/',
    '/website/static/',
    '/longpolling/',
    '/web_editor/',
    '/_health',
)


class IrHttp(models.AbstractModel):
    _inherit = 'ir.http'

    # ------------------------------------------------------------------
    # Public Odoo override
    # ------------------------------------------------------------------

    @classmethod
    def _serve_fallback(cls):
        """Called by Odoo when no route or page matched the request path.

        Tries ERA SEO redirects first; on miss, records a 404 log entry and
        defers to ``super()._serve_fallback()``.
        """
        if not (request and getattr(request, 'httprequest', None)):
            return super()._serve_fallback()

        # Skip non-GET/HEAD methods. POST/PUT/PATCH/DELETE on missing paths
        # are usually API calls; redirecting them silently breaks clients.
        method = (request.httprequest.method or '').upper()
        if method not in ('GET', 'HEAD'):
            return super()._serve_fallback()

        path = cls._era_path()
        if path is None:
            return super()._serve_fallback()

        # Skip system paths — never redirect /web/, /my/, /static/, etc.
        if cls._era_is_system_path(path):
            return super()._serve_fallback()

        # 1. Redirect match?
        response = cls._era_try_redirect(path)
        if response is not None:
            return response

        # 2. Miss: log + 404 fallback.
        cls._era_log_404(path)
        return super()._serve_fallback()

    # ------------------------------------------------------------------
    # Redirect resolution
    # ------------------------------------------------------------------

    @classmethod
    def _era_path(cls):
        """Return the normalized request path, or None if we should bail out.

        Strips the language URL prefix (``/ar``, ``/en``, ...) when one is
        present so a rule authored against ``/old`` matches both ``/old`` and
        ``/ar/old``. The lang itself is resolved separately via
        ``_era_lang()`` so per-language rules still work.
        """
        try:
            raw = request.httprequest.path or '/'
        except Exception:  # noqa: BLE001
            return None
        Redirect = request.env['era.seo.redirect'].sudo()
        path = Redirect._normalize_path(raw)
        return cls._era_strip_lang_prefix(path)

    @classmethod
    def _era_is_system_path(cls, path):
        return any(path == p.rstrip('/') or path.startswith(p)
                   for p in _SYSTEM_PATH_PREFIXES)

    @classmethod
    def _era_strip_lang_prefix(cls, path):
        """Drop the active language URL prefix from ``path`` when present.

        Looks at the website's installed languages and removes a matching
        leading segment. Returns the original path when nothing matches so
        rules authored with an explicit prefix still work.
        """
        if not path or path == '/':
            return path
        try:
            website = request.env['website'].get_current_website()
            languages = website.language_ids if website else None
        except Exception:  # noqa: BLE001
            languages = None
        if not languages:
            return path
        for lang in languages:
            code = (lang.url_code or '').strip('/')
            if not code:
                continue
            prefix = '/' + code
            if path == prefix:
                return '/'
            if path.startswith(prefix + '/'):
                return path[len(prefix):]
        return path

    @classmethod
    def _era_try_redirect(cls, path):
        """Look up a redirect rule for ``path``; return a Response or None.

        Performs the lookup twice when needed — once with the path as
        normalized and once with the trailing slash toggled — so
        ``/foo`` and ``/foo/`` are treated as the same source URL.
        """
        env = request.env
        Redirect = env['era.seo.redirect'].sudo()
        website = cls._era_website()
        lang = cls._era_lang()

        rule, target = Redirect._find_match(path, website=website, lang=lang)
        if not rule:
            alt = cls._era_toggle_trailing_slash(path)
            if alt and alt != path:
                rule, target = Redirect._find_match(alt, website=website, lang=lang)
        if not rule:
            return None

        # 410 Gone: short-circuit before touching the hop counter.
        if rule.redirect_type == '410':
            rule._register_hit()
            return werkzeug.wrappers.Response(
                'This resource is gone.',
                status=410,
                content_type='text/plain; charset=utf-8',
            )

        # Hop counter: prevent runaway redirect chains.
        hops = cls._era_hop_count()
        if hops >= REDIRECT_HOP_LIMIT:
            _logger.warning(
                'era.seo.redirect: hop limit %d exceeded for path %r '
                '(rule #%d -> %r); returning 508.',
                REDIRECT_HOP_LIMIT, path, rule.id, target,
            )
            response = werkzeug.wrappers.Response(
                'Redirect loop detected.',
                status=508,
                content_type='text/plain; charset=utf-8',
            )
            response.delete_cookie(_HOP_COOKIE)
            return response

        rule._register_hit()
        final_target = cls._era_forward_query_string(target)
        response = werkzeug.utils.redirect(final_target, code=int(rule.redirect_type))
        response.set_cookie(
            _HOP_COOKIE,
            str(hops + 1),
            max_age=_HOP_COOKIE_MAX_AGE,
            httponly=True,
            samesite='Lax',
        )
        return response

    @classmethod
    def _era_toggle_trailing_slash(cls, path):
        """Return ``path`` with its trailing slash toggled, or None for root."""
        if not path or path == '/':
            return None
        if path.endswith('/'):
            return path.rstrip('/')
        return path + '/'

    @classmethod
    def _era_forward_query_string(cls, target):
        """Append the inbound request's query string to ``target`` when it
        doesn't already carry one. Keeps UTM/affiliate/etc parameters alive
        across the hop.

        If the target is an absolute URL we still forward — Werkzeug accepts
        either path or URL.
        """
        if not target:
            return target
        try:
            qs = (request.httprequest.query_string or b'').decode('latin-1')
        except Exception:  # noqa: BLE001
            qs = ''
        if not qs:
            return target
        if '?' in target:
            # Author opted into their own query string; merge by appending
            # the inbound params after a '&' so both sets survive.
            return target + '&' + qs
        return target + '?' + qs

    # ------------------------------------------------------------------
    # 404 logging
    # ------------------------------------------------------------------

    @classmethod
    def _era_log_404(cls, path):
        """Best-effort: record the unmatched path. Never raises.

        Uses a dedicated cursor so the write commits independently of the main
        request transaction.  Odoo rolls back that transaction for every 404
        response (NotFound is raised in _serve_ir_http_fallback after
        _serve_fallback returns None), which would silently discard any write
        made on the request cursor.
        """
        try:
            website = cls._era_website()
            website_id = website.id if website else False
            referer = request.httprequest.headers.get('Referer')
            user_agent = request.httprequest.headers.get('User-Agent', '')
            with request.env.registry.cursor() as cr:
                env = request.env(cr=cr)
                Log = env['era.seo.redirect.log'].sudo()
                ws = env['website'].browse(website_id) if website_id else None
                Log._record_miss(path, website=ws, referer=referer, user_agent=user_agent)
                cr.commit()
        except Exception as exc:  # noqa: BLE001
            _logger.debug('era.seo.redirect.log: skipped (%s)', exc)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @classmethod
    def _era_website(cls):
        """Resolve the active website for the request, or None."""
        try:
            return request.env['website'].get_current_website()
        except Exception:  # noqa: BLE001
            return None

    @classmethod
    def _era_lang(cls):
        """Resolve the active res.lang for the request, or None."""
        try:
            lang_code = request.lang and request.lang.code
            if not lang_code:
                return None
            return request.env['res.lang'].sudo().search(
                [('code', '=', lang_code)], limit=1,
            ) or None
        except Exception:  # noqa: BLE001
            return None

    @classmethod
    def _era_hop_count(cls):
        """Return the current redirect-hop count from the request cookie."""
        try:
            raw = request.httprequest.cookies.get(_HOP_COOKIE, '0')
            return max(0, int(raw))
        except (TypeError, ValueError):
            return 0

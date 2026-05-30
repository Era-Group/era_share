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
import difflib
import logging
import time

import werkzeug

from odoo import models
from odoo.http import request

from .seo_redirect import REDIRECT_HOP_LIMIT

_logger = logging.getLogger(__name__)

# Smart-404 ("did you mean") defaults — overridable via ir.config_parameter:
#   era_seo.smart_404_enabled        bool   master switch for fuzzy matching
#   era_seo.smart_404_threshold      float  min similarity (0-1) to redirect
#   era_seo.smart_404_home_fallback  bool   redirect unmatched page-misses home
_SMART_404_DEFAULT_THRESHOLD = 0.72
# Per-worker cache of known published URLs, keyed by website id:
#   {website_id: (expiry_monotonic, [(url, last_segment_slug), ...])}
_KNOWN_URLS_CACHE = {}
_KNOWN_URLS_TTL = 300  # seconds

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

        # 1. Admin-managed redirect rule?
        response = cls._era_try_redirect(path)
        if response is not None:
            return response

        # 2. Smart "did you mean": redirect to the closest existing published
        #    URL when one is similar enough. Skips asset-like paths (missing
        #    files, not mistyped pages) and obeys the loop hop-counter.
        if cls._era_smart_404_enabled() and not cls._era_is_asset_like(path):
            match = cls._era_fuzzy_match(path)
            if match and match.rstrip('/') != path.rstrip('/'):
                smart = cls._era_smart_redirect(match, 302)
                if smart is not None:
                    _logger.info('smart-404: %r ~> %r', path, match)
                    return smart

        # 3. Miss: log it. Then, for page-like paths, redirect home (opt-out
        #    via era_seo.smart_404_home_fallback). Asset-like misses and the
        #    home path itself fall through to the normal 404.
        cls._era_log_404(path)
        if (cls._era_smart_404_home_fallback()
                and not cls._era_is_asset_like(path)
                and path.rstrip('/') != ''):
            home = cls._era_smart_redirect('/', 302)
            if home is not None:
                return home
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

    # ------------------------------------------------------------------
    # Smart 404 ("did you mean") — fuzzy match to a known published URL
    # ------------------------------------------------------------------

    @staticmethod
    def _era_is_asset_like(path):
        """True for paths whose last segment has a file extension (icons,
        images, .php/.env probes, ...). Those are missing files, not mistyped
        pages — fuzzy-matching or home-redirecting them makes no sense."""
        last = (path or '').rstrip('/').rsplit('/', 1)[-1]
        return '.' in last

    @classmethod
    def _era_smart_404_enabled(cls):
        return cls._era_icp_bool('era_seo.smart_404_enabled', True)

    @classmethod
    def _era_smart_404_home_fallback(cls):
        return cls._era_icp_bool('era_seo.smart_404_home_fallback', True)

    @staticmethod
    def _era_icp_bool(key, default):
        try:
            val = request.env['ir.config_parameter'].sudo().get_param(key)
        except Exception:  # noqa: BLE001
            return default
        if val is None or val == '':
            return default
        return str(val).strip().lower() in ('1', 'true', 'yes', 'on')

    @classmethod
    def _era_smart_404_threshold(cls):
        try:
            raw = request.env['ir.config_parameter'].sudo().get_param(
                'era_seo.smart_404_threshold')
            t = float(raw) if raw not in (None, '') else _SMART_404_DEFAULT_THRESHOLD
        except (TypeError, ValueError):
            t = _SMART_404_DEFAULT_THRESHOLD
        # Clamp to a sane band so a bad config can't make EVERY 404 redirect
        # (too low) or never match (too high).
        return min(0.95, max(0.5, t))

    @classmethod
    def _era_known_urls(cls, website_id):
        """Return a cached list of (url, last_segment_slug) for published pages
        and blog posts — the 'existing working links' to match against.

        Cached per worker for ``_KNOWN_URLS_TTL`` seconds, keyed by website, so
        a 404 burst (scanner sweep) doesn't re-query the catalogue each hit.
        """
        now = time.monotonic()
        cached = _KNOWN_URLS_CACHE.get(website_id)
        if cached and cached[0] > now:
            return cached[1]
        urls = []
        try:
            env = request.env
            Page = env['website.page'].sudo()
            dom = [('is_published', '=', True)]
            if website_id:
                dom.append(('website_id', 'in', [website_id, False]))
            for url in Page.search(dom).mapped('url'):
                u = (url or '').strip()
                if u.startswith('/') and not cls._era_is_system_path(u) \
                        and not cls._era_is_asset_like(u):
                    urls.append((u, u.rstrip('/').rsplit('/', 1)[-1].lower()))
            BlogPost = env.get('blog.post')
            if BlogPost is not None:
                for post in BlogPost.sudo().search([('is_published', '=', True)]):
                    try:
                        u = (post.website_url or '').strip()
                    except Exception:  # noqa: BLE001
                        continue
                    if u.startswith('/') and not cls._era_is_asset_like(u):
                        urls.append(
                            (u, u.rstrip('/').rsplit('/', 1)[-1].lower()))
        except Exception as exc:  # noqa: BLE001
            _logger.debug('smart-404: known-url build failed (%s)', exc)
            return []
        _KNOWN_URLS_CACHE[website_id] = (now + _KNOWN_URLS_TTL, urls)
        return urls

    @classmethod
    def _era_fuzzy_match(cls, path):
        """Return the closest published URL to ``path`` whose similarity clears
        the threshold, or None. Scores both the whole path and the final slug
        (the meaningful part) and keeps the better of the two."""
        want = (path or '').strip('/').lower()
        if not want:
            return None
        want_slug = want.rsplit('/', 1)[-1]
        website = cls._era_website()
        candidates = cls._era_known_urls(website.id if website else False)
        if not candidates:
            return None
        threshold = cls._era_smart_404_threshold()
        sm = difflib.SequenceMatcher()
        best_url, best_score = None, 0.0
        for url, slug in candidates:
            # Score the final slug (the meaningful part) and the whole path;
            # keep the better. quick_ratio() is a cheap upper bound used to
            # skip the full ratio() when it can't beat the best so far.
            sm.set_seqs(want_slug, slug)
            score = sm.ratio()
            sm.set_seqs(want, url.strip('/').lower())
            if sm.quick_ratio() > score:
                score = max(score, sm.ratio())
            if score > best_score:
                best_url, best_score = url, score
        return best_url if best_score >= threshold else None

    @classmethod
    def _era_smart_redirect(cls, target, code):
        """Build a Werkzeug redirect to ``target`` with the loop hop-counter
        cookie, or None when the hop limit is already reached (let it 404)."""
        hops = cls._era_hop_count()
        if hops >= REDIRECT_HOP_LIMIT:
            return None
        final = cls._era_forward_query_string(target)
        response = werkzeug.utils.redirect(final, code=code)
        response.set_cookie(
            _HOP_COOKIE, str(hops + 1), max_age=_HOP_COOKIE_MAX_AGE,
            httponly=True, samesite='Lax',
        )
        return response

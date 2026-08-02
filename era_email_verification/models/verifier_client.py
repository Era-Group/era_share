"""The single outbound network seam to the private Email Verifier API.

Design guarantees (mirrors the security brief):

* **SSRF-safe** — every call targets the ONE administrator-configured base URL.
  The host is never taken from user input, redirects are refused, and only
  ``https`` is accepted (plain ``http`` allowed solely for an explicit
  loopback, e.g. when Odoo and the verifier share a host).
* **Secret-safe** — the Bearer key is read from ``ir.config_parameter`` via
  ``sudo`` and is NEVER logged or returned. Errors log the host + exception
  class only.
"""
import logging
from urllib.parse import urlparse

from odoo import _, api, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# Loopback hosts may use plain http; every other host must use https.
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
_REDIRECT_CODES = {301, 302, 303, 307, 308}


def _to_float(value, default):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class EmailVerificationClient(models.AbstractModel):
    _name = "email.verification.client"
    _description = "Email Verifier API Client"

    # -- configuration --------------------------------------------------------
    @api.model
    def _get_config(self):
        icp = self.env["ir.config_parameter"].sudo()
        base_url = (icp.get_param("era_email_verification.base_url") or "").strip().rstrip("/")
        api_key = (icp.get_param("era_email_verification.api_key") or "").strip()
        verify_tls = str(icp.get_param("era_email_verification.verify_tls", "True")).strip().lower()
        return {
            "base_url": base_url,
            "api_key": api_key,
            "verify_tls": verify_tls in ("1", "true", "yes", "on"),
            "timeout": (
                _to_float(icp.get_param("era_email_verification.timeout_connect"), 5.0),
                _to_float(icp.get_param("era_email_verification.timeout_read"), 30.0),
            ),
        }

    @api.model
    def _validate_base_url(self, base_url):
        if not base_url:
            raise UserError(_(
                "The Email Verifier base URL is not configured. "
                "Set it in Settings ▸ Email Verification."))
        parsed = urlparse(base_url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise UserError(_("The Email Verifier base URL is invalid: %s") % base_url)
        if parsed.scheme == "http" and parsed.hostname not in _LOOPBACK_HOSTS:
            raise UserError(_(
                "The Email Verifier base URL must use HTTPS "
                "(plain HTTP is only allowed for localhost)."))
        return parsed

    # -- low-level request ----------------------------------------------------
    @api.model
    def _request(self, method, path, payload=None, params=None, extra_headers=None):
        """Perform one request against the configured verifier. Returns the
        ``requests.Response`` on 2xx, ``None`` on 404, and raises ``UserError``
        (with a redacted message) on any other failure."""
        cfg = self._get_config()
        if not cfg["api_key"]:
            raise UserError(_(
                "The Email Verifier API key is not configured. "
                "Set it in Settings ▸ Email Verification."))
        parsed = self._validate_base_url(cfg["base_url"])
        host = parsed.hostname
        url = cfg["base_url"] + path
        try:
            import requests  # lazy import keeps module import side-effect free
        except ImportError:  # pragma: no cover - requests ships with Odoo
            raise UserError(_("The Python 'requests' library is required."))
        headers = {
            "Authorization": "Bearer %s" % cfg["api_key"],
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        headers.update(extra_headers or {})
        try:
            resp = requests.request(
                method, url, json=payload, params=params, headers=headers,
                timeout=cfg["timeout"], verify=cfg["verify_tls"], allow_redirects=False,
            )
        except Exception as exc:  # noqa: BLE001 - never leak URL/key
            _logger.warning(
                "Email Verifier %s %s -> transport error (%s)",
                method, path, type(exc).__name__)
            raise UserError(_(
                "Could not reach the Email Verifier service (%s).") % type(exc).__name__)
        if resp.status_code in _REDIRECT_CODES:
            _logger.warning("Email Verifier at %s returned a redirect — refused.", host)
            raise UserError(_("The Email Verifier returned an unexpected redirect."))
        if resp.status_code == 401:
            raise UserError(_("The Email Verifier rejected the API key (HTTP 401)."))
        if resp.status_code == 404:
            return None
        if resp.status_code >= 400:
            _logger.warning("Email Verifier %s %s -> HTTP %s", method, path, resp.status_code)
            raise UserError(_(
                "The Email Verifier returned HTTP %s.") % resp.status_code)
        return resp

    # -- public API -----------------------------------------------------------
    @api.model
    @api.private
    def verify_one(self, email, check_smtp=True, check_catch_all=True):
        resp = self._request("POST", "/v1/verify", payload={
            "email": email,
            "check_smtp": check_smtp,
            "check_catch_all": check_catch_all,
        })
        return resp.json()

    @api.model
    @api.private
    def create_job(self, emails, check_smtp=True, check_catch_all=True,
                   callback_url=None, callback_secret=None, idempotency_key=None):
        """POST a bulk job. Returns ``{job_id, status, total}``.

        When ``callback_url`` is given the verifier will push completed results
        to it (HMAC-signed with ``callback_secret``); polling still works as a
        fallback regardless.
        """
        payload = {
            "emails": list(emails),
            "check_smtp": check_smtp,
            "check_catch_all": check_catch_all,
        }
        if callback_url and callback_secret:
            payload["callback_url"] = callback_url
            payload["callback_secret"] = callback_secret
        extra_headers = (
            {"Idempotency-Key": idempotency_key} if idempotency_key else None)
        resp = self._request(
            "POST", "/v1/jobs", payload=payload, extra_headers=extra_headers)
        return resp.json()

    @api.model
    @api.private
    def get_job(self, job_id):
        """Return the job dict (``status`` == 'done' when finished) or None."""
        resp = self._request("GET", "/v1/jobs/%s" % job_id)
        return resp.json() if resp is not None else None

    @api.model
    @api.private
    def get_results(self, job_id, offset=0, limit=500):
        """Return one page ``{job_id, offset, limit, items:[...]}`` or None."""
        resp = self._request(
            "GET", "/v1/jobs/%s/results" % job_id,
            params={"offset": offset, "limit": limit})
        return resp.json() if resp is not None else None

    @api.model
    @api.private
    def delete_job(self, job_id):
        """Purge the remote job. True if it existed (204), False on 404."""
        resp = self._request("DELETE", "/v1/jobs/%s" % job_id)
        return resp is not None

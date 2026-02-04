# -*- coding: utf-8 -*-
import json
import logging
import time
from typing import Dict, List, Optional

import requests

from odoo import _, api, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class EraSpyClient(models.AbstractModel):
    _name = "eraspy.client"
    _description = "EraSpy API Client"

    _last_call_ts = 0.0

    def _get_rate_limit_block_until(self):
        ICP = self.env["ir.config_parameter"].sudo()
        value = ICP.get_param("era_spy.rate_limit_block_until")
        try:
            return float(value) if value else 0.0
        except (TypeError, ValueError):
            return 0.0

    def _set_rate_limit_block_until(self, seconds: int):
        ICP = self.env["ir.config_parameter"].sudo()
        block_until = time.time() + max(int(seconds or 0), 0)
        ICP.set_param("era_spy.rate_limit_block_until", str(block_until))

    def _raise_if_rate_limited(self):
        block_until = self._get_rate_limit_block_until()
        now = time.time()
        if block_until and now < block_until:
            remaining = int(block_until - now)
            if remaining < 1:
                remaining = 1
            raise UserError(
                _("EraSpy rate limit reached. Please wait %s seconds and try again.") % remaining
            )

    def _get_config(self):
        ICP = self.env["ir.config_parameter"].sudo()
        base_url = ICP.get_param("era_spy.base_url", default="https://spy.era.net.sa/api")
        callback_url = ICP.get_param("era_spy.callback_url")
        if not callback_url:
            system_base = ""
            try:
                from odoo.http import request
            except Exception:
                request = None
            if request and getattr(request, "httprequest", None):
                try:
                    system_base = (request.httprequest.url_root or "").rstrip("/")
                except Exception:
                    system_base = ""
                if not system_base:
                    host = request.httprequest.headers.get("X-Forwarded-Host") or request.httprequest.host
                    proto = request.httprequest.headers.get("X-Forwarded-Proto") or request.httprequest.scheme
                    if host:
                        system_base = f"{proto}://{host}".rstrip("/")
            if not system_base:
                system_base = ICP.get_param("web.base.url", default="").rstrip("/")
            if not system_base:
                raise UserError(
                    _("Set the system base URL in General Settings so EraSpy callbacks can reach Odoo.")
                )
            callback_url = f"{system_base}/eraspy/callback"
        try:
            rate_limit = int(ICP.get_param("era_spy.rate_limit_per_minute", default=600))
        except ValueError:
            rate_limit = 600
        without_contacts_default = ICP.get_param("era_spy.without_contacts_default") == "True"
        return {
            "base_url": base_url.rstrip("/"),
            "callback_url": callback_url.strip(),
            "rate_limit": rate_limit if rate_limit > 0 else 600,
            "without_contacts_default": without_contacts_default,
        }

    def _throttle(self, rate_limit: int):
        min_interval = 60.0 / rate_limit if rate_limit else 0
        now = time.monotonic()
        last_ts = type(self)._last_call_ts
        if min_interval and last_ts:
            wait = last_ts + min_interval - now
            if wait > 0:
                time.sleep(wait)
        type(self)._last_call_ts = time.monotonic()

    def _request(self, method: str, path: str, payload: Optional[dict] = None, params: Optional[dict] = None):
        self._raise_if_rate_limited()
        cfg = self._get_config()
        self._throttle(cfg["rate_limit"])

        url = f'{cfg["base_url"]}/{path.lstrip("/")}'
        payload = payload or {}
        params = params or {}
        if payload:
            try:
                payload_dump = json.dumps(payload, ensure_ascii=False, default=str)
            except Exception:
                payload_dump = str(payload)
            _logger.info("EraSpy request payload: %s", payload_dump)
        if "items" in payload:
            _logger.info(
                "EraSpy request %s %s items=%s",
                method.upper(),
                url,
                len(payload.get("items") or []),
            )
        else:
            _logger.info("EraSpy request %s %s", method.upper(), url)
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        try:
            response = requests.request(
                method=method.upper(),
                url=url,
                headers=headers,
                json=payload,
                params=params,
                timeout=30,
            )
        except Exception as exc:
            _logger.exception("EraSpy request failed to %s", url)
            raise UserError(_("EraSpy request failed: %s") % exc)

        if response.status_code == 401:
            raise UserError(_("EraSpy authentication failed (401)."))
        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            try:
                retry_after_seconds = int(retry_after) if retry_after else 60
            except (TypeError, ValueError):
                retry_after_seconds = 60
            self._set_rate_limit_block_until(retry_after_seconds)
            raise UserError(
                _("EraSpy rate limit reached (429). Please wait %s seconds and try again.")
                % retry_after_seconds
            )
        if response.status_code >= 500:
            raise UserError(_("EraSpy service is temporarily unavailable (status %s).") % response.status_code)

        try:
            data = response.json()
        except json.JSONDecodeError:
            _logger.error("EraSpy returned non-JSON response: %s", response.text[:2000])
            lowered = (response.text or "").lower()
            if "blocked" in lowered or "forbidden" in lowered or response.status_code in (401, 403):
                raise UserError(_("Access to EraSpy is blocked. Contact info@era.net.sa to activate your account."))
            raise UserError(_("EraSpy returned an unexpected response. Please try again later. Status: %s") % response.status_code)

        if data.get("error"):
            raise UserError(_("EraSpy error: %s") % data.get("error"))

        if response.status_code >= 400:
            msg = data.get("message") or data
            raise UserError(_("EraSpy error: %s") % msg)

        # Include the HTTP status for downstream context.
        data["status_code"] = response.status_code
        if data.get("requestId"):
            _logger.info("EraSpy request accepted: requestId=%s status=%s", data.get("requestId"), response.status_code)
        return data

    def _call_ai_agent(self, prompt: str, body: str, timeout: int = 25) -> str:
        ICP = self.env["ir.config_parameter"].sudo()
        url = (ICP.get_param("era_spy.ai_agent_url") or "").strip()
        if not url:
            url = "https://ai1.era.net.sa"
        if "://" not in url:
            url = f"https://{url}"
        if not url:
            return ""
        merged = ""
        if prompt:
            merged = prompt.strip()
        if body:
            merged = f"{merged}\n\n{body}".strip() if merged else str(body)
        _logger.info("EraSpy AI agent request: url=%s timeout=%s body_len=%s", url, timeout, len(merged or ""))
        headers = {
            "Content-Type": "text/plain; charset=utf-8",
        }
        payload = (merged or "").encode("utf-8")
        try:
            response = requests.post(url, headers=headers, data=payload, timeout=timeout)
        except Exception as exc:
            _logger.warning("EraSpy AI agent request failed: %s", exc)
            return ""
        _logger.info(
            "EraSpy AI agent response: status=%s content_type=%s body_len=%s",
            response.status_code,
            response.headers.get("Content-Type"),
            len(response.text or ""),
        )
        if response.status_code >= 400:
            _logger.warning(
                "EraSpy AI agent response failed: status=%s body=%s",
                response.status_code,
                (response.text or "")[:1000],
            )
            return ""
        content_type = (response.headers.get("Content-Type") or "").lower()
        if "application/json" in content_type:
            try:
                data = response.json()
            except Exception:
                return (response.text or "").strip()
            if isinstance(data, dict):
                text = data.get("result") or data.get("text") or data.get("message") or data.get("output")
                if text is None:
                    text = json.dumps(data, ensure_ascii=False)
                return str(text).strip()
        return (response.text or "").strip()

    # Public API wrappers
    def search_person(self, identifiers: List[str], without_contacts: bool = False, callback_url: Optional[str] = None):
        """POST /v1/candidate/search with identifiers (email/phone/linkedin)."""
        if isinstance(identifiers, (str, bytes)):
            identifiers = [identifiers]
        elif isinstance(identifiers, tuple):
            identifiers = list(identifiers)
        if not identifiers:
            raise UserError(_("Provide at least one email, phone, or LinkedIn URL."))
        identifiers = [item for item in identifiers if item]
        if not identifiers:
            raise UserError(_("Provide at least one email, phone, or LinkedIn URL."))

        cfg = self._get_config()
        payload = {
            "items": identifiers,
            "callbackUrl": callback_url or cfg["callback_url"],
        }
        if without_contacts:
            payload["withoutContacts"] = True
        return self._request("POST", "/v1/candidate/search", payload)

    def search_by_query(self, query: str, size: int = 20, page: int = 0, without_contacts: bool = False):
        """POST /v1/candidate/searchByQuery for prospecting."""
        cfg = self._get_config()
        payload = {
            "query": query,
            "size": size,
            "page": page,
        }
        if without_contacts:
            payload["withoutContacts"] = True
        if cfg.get("callback_url"):
            payload["callbackUrl"] = cfg["callback_url"]
        return self._request("POST", "/v1/candidate/searchByQuery", payload)

    def get_credits(self) -> Dict[str, Optional[int]]:
        """GET /v1/credits to retrieve remaining credits."""
        data = self._request("GET", "/v1/credits")
        return {
            "credits_left": data.get("creditsLeft") or data.get("credits") or data.get("remaining"),
            "status": data.get("status_code"),
            "message": data.get("message"),
        }

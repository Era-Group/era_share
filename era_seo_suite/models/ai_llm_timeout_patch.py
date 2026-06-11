"""Stretch the Odoo EE AI app's hard-coded 30s HTTP read timeout.

Why this exists
---------------
`odoo.addons.ai.utils.llm_api_service.LLMApiService._request` is declared with
``timeout: int = 30`` (see /opt/odoo/ee/ai/utils/llm_api_service.py:234). The
helper is called WITHOUT an explicit timeout from every prompt path
(``_request_llm_openai_helper``, ``_request_llm_google_helper``, …) so every
LLM call effectively has a 30s ceiling.

That's tight for `gpt-4o`/`gpt-4.1` on multi-language SEO fill prompts —
seen in production as a flood of identical
``ReadTimeout (read timeout=30)`` log errors when bulk-fill sweeps a site.

Raising the call-site timeout is the surgical fix, but it would mean
forking the EE call sites. Easier: monkey-patch the helper at module
import so every caller gets a roomier default. The patch reads the
ceiling from an ICP key (``era_seo.ai_request_timeout``, default 180s)
so admins can tune without touching code.

Coexistence with era_odoo_ai_ext
--------------------------------
era_odoo_ai_ext monkey-patches LLMApiService._request with its own
version (retry loop + custom_llm provider). Load order between the two
modules is not guaranteed, so this patch resolves the underlying method
at **call time** via ``LLMApiService`` class lookup rather than
capturing a stale reference at import time. This way, regardless of
which module patches first, the timeout wrapper always delegates to
whatever _request implementation is currently live.
"""
import logging

_logger = logging.getLogger(__name__)


_PATCH_FLAG = '_era_seo_timeout_patched'
_ORIGINAL_ATTR = '_era_seo_original_request'
_DEFAULT_TIMEOUT_S = 180
_ICP_KEY = 'era_seo.ai_request_timeout'


def _resolve_timeout(env_or_none):
    """Best-effort: read the override from ICP, fall back to the default.

    ``env`` is not always available at the patched call site (the patched
    method is on a non-model class), but it carries an ``env`` attribute.
    Defensive — any read failure falls back to the default.
    """
    try:
        if env_or_none is None:
            return _DEFAULT_TIMEOUT_S
        raw = env_or_none['ir.config_parameter'].sudo().get_param(
            _ICP_KEY, str(_DEFAULT_TIMEOUT_S))
        return max(30, int(raw))
    except Exception:  # noqa: BLE001
        return _DEFAULT_TIMEOUT_S


def _apply_patch():
    try:
        from odoo.addons.ai.utils.llm_api_service import LLMApiService
    except ImportError:
        _logger.info(
            'ai_llm_timeout_patch: ai app not importable, skipping patch')
        return

    if getattr(LLMApiService, _PATCH_FLAG, False):
        return  # already wrapped

    original_request = LLMApiService._request
    setattr(LLMApiService, _ORIGINAL_ATTR, original_request)

    def _request_with_stretched_timeout(self, method, endpoint, headers, body,
                                        data=None, files=None, params=None,
                                        base_url=None, timeout=30):
        if timeout == 30:
            timeout = _resolve_timeout(getattr(self, 'env', None))
        # Resolve the real implementation at call time so that if
        # era_odoo_ai_ext (or another module) replaced _request after us,
        # we delegate to their version, not the stale original.
        real_request = getattr(LLMApiService, _ORIGINAL_ATTR, original_request)
        current = LLMApiService._request
        if current is not _request_with_stretched_timeout:
            # Another module overwrote _request after our patch.
            # Re-wrap: save their version and reinstall ourselves.
            setattr(LLMApiService, _ORIGINAL_ATTR, current)
            LLMApiService._request = _request_with_stretched_timeout
            real_request = current
        return real_request(
            self, method, endpoint, headers, body,
            data=data, files=files, params=params,
            base_url=base_url, timeout=timeout,
        )

    LLMApiService._request = _request_with_stretched_timeout
    setattr(LLMApiService, _PATCH_FLAG, True)
    _logger.info(
        'ai_llm_timeout_patch: LLMApiService._request now reads timeout from '
        'ICP %s (default %ds)', _ICP_KEY, _DEFAULT_TIMEOUT_S)


_apply_patch()

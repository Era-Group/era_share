# -*- coding: utf-8 -*-
from odoo.addons.ai.utils.llm_api_service import LLMApiService as BaseLLMApiService

DEFAULT_LLM_TIMEOUT = 600
_ORIGINAL_REQUEST = BaseLLMApiService._request


def _patched_request(
    self,
    method: str,
    endpoint: str,
    headers: dict[str, str],
    body: dict,
    data: dict | None = None,
    files: dict | None = None,
    params: dict | None = None,
    base_url: str | None = None,
    timeout: int = 30,
) -> dict:
    return _ORIGINAL_REQUEST(
        self,
        method,
        endpoint,
        headers,
        body,
        data=data,
        files=files,
        params=params,
        base_url=base_url,
        timeout=DEFAULT_LLM_TIMEOUT,
    )


if not getattr(BaseLLMApiService, "_era_timeout_patched", False):
    BaseLLMApiService._request = _patched_request
    BaseLLMApiService._era_timeout_patched = True

from odoo.addons.ai.utils.llm_api_service import LLMApiService as BaseLLMApiService


class LLMApiService(BaseLLMApiService):
    def _request(
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
        return super()._request(
            method,
            endpoint,
            headers,
            body,
            data=data,
            files=files,
            params=params,
            base_url=base_url,
            timeout=600,
        )

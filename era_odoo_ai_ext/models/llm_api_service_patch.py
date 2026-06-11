import os
import ast
import json
import re
import time
import inspect
import requests

from odoo import _
from odoo.exceptions import UserError

from odoo.addons.ai.utils.llm_api_service import LLMApiService

from .llm_providers_patch import CUSTOM_LLM_EMBEDDING_KEY, CUSTOM_LLM_MODEL_KEY


def _custom_llm_param(env, key, default=None):
    return env["ir.config_parameter"].sudo().get_param(key, default)


def _resolve_custom_model(service, model_name, is_embedding=False):
    if service.provider != "custom_llm":
        return model_name

    if is_embedding and model_name == CUSTOM_LLM_EMBEDDING_KEY:
        return _custom_llm_param(service.env, "ai.custom_llm_embedding_model") or None

    if not is_embedding and model_name == CUSTOM_LLM_MODEL_KEY:
        return _custom_llm_param(service.env, "ai.custom_llm_model", "openrouter/free")

    return model_name


def _custom_llm_retry_models(env):
    configured_models = [
        _custom_llm_param(env, "ai.custom_llm_model", "openrouter/free"),
        _custom_llm_param(env, "ai.custom_llm_model_2"),
        _custom_llm_param(env, "ai.custom_llm_model_3"),
        _custom_llm_param(env, "ai.custom_llm_model_4"),
    ]
    models = []
    for model in configured_models:
        if model and model not in models:
            models.append(model)
    return models


def _coerce_tool_arguments_for_schema(arguments, tool_schema):
    """Coerce provider tool arguments to declared schema types when possible.

    Some OpenAI-compatible gateways may return loosely-typed tool args even when
    schema is provided (e.g., dict/list for a field declared as string).
    """
    if not isinstance(arguments, dict) or not isinstance(tool_schema, dict):
        return arguments

    properties = tool_schema.get("properties") or {}
    for key, value in list(arguments.items()):
        field = properties.get(key) or {}
        declared_type = field.get("type")
        allowed_types = declared_type if isinstance(declared_type, list) else [declared_type]
        allowed_types = {t for t in allowed_types if isinstance(t, str)}
        if not allowed_types:
            continue

        if "string" in allowed_types and value is not None and not isinstance(value, str):
            if isinstance(value, (dict, list)):
                arguments[key] = json.dumps(value, ensure_ascii=False)
            else:
                arguments[key] = str(value)
            continue

        if "boolean" in allowed_types and isinstance(value, str):
            low = value.strip().lower()
            if low in {"true", "1", "yes", "on"}:
                arguments[key] = True
            elif low in {"false", "0", "no", "off"}:
                arguments[key] = False
            continue

        if "integer" in allowed_types and isinstance(value, str):
            try:
                arguments[key] = int(value)
            except ValueError:
                pass
            continue

        if "number" in allowed_types and isinstance(value, str):
            try:
                arguments[key] = float(value)
            except ValueError:
                pass

    return arguments


def _autofill_required_tool_arguments(arguments, tool_schema, env_context):
    """Best-effort fill for frequently missing required args in AI menu tools."""
    if not isinstance(arguments, dict) or not isinstance(tool_schema, dict):
        return arguments

    required = tool_schema.get("required") or []
    if not required:
        return arguments

    current_view_info = env_context.get("current_view_info") or {}
    menu_id = (
        current_view_info.get("menu_id")
        or current_view_info.get("menuId")
        or env_context.get("menu_id")
    )
    model_name = (
        current_view_info.get("model_name")
        or current_view_info.get("model")
        or env_context.get("active_model")
    )

    candidates = {
        "menu_id": menu_id,
        "menu_ids": [menu_id] if menu_id else None,
        "model_name": model_name,
    }

    for key in required:
        if key not in arguments or arguments.get(key) in (None, "", []):
            if candidates.get(key) not in (None, "", []):
                arguments[key] = candidates[key]

    return arguments


def _strip_markdown_fences(text):
    if not isinstance(text, str):
        return text
    candidate = text.strip()
    if candidate.startswith("```") and candidate.endswith("```"):
        lines = candidate.splitlines()
        if len(lines) >= 3:
            return "\n".join(lines[1:-1]).strip()
    return candidate


def _parse_json_like_payload(text):
    """Parse JSON-like payloads used by some OpenAI-compatible gateways."""
    if not isinstance(text, str):
        return None

    raw = _strip_markdown_fences(text)
    if not raw:
        return None

    candidates = [raw]
    normalized = re.sub(r"^\s*toolcalls?\s*>\s*", "", raw, flags=re.IGNORECASE).strip()
    normalized = re.sub(r"\s*>+\s*$", "", normalized).strip()
    if normalized and normalized != raw:
        candidates.append(normalized)
    if '\\"' in raw:
        candidates.append(raw.replace('\\"', '"'))
    if '\\"' in normalized:
        candidates.append(normalized.replace('\\"', '"'))

    seen = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        try:
            return json.loads(candidate)
        except Exception:  # noqa: BLE001
            pass
        if candidate[:1] in {"{", "["}:
            try:
                return ast.literal_eval(candidate)
            except Exception:  # noqa: BLE001
                pass
    return None


def _extract_tool_calls_from_text(text, tools, env_context):
    """Extract tool calls when provider emits them as plain text JSON."""
    parsed = _parse_json_like_payload(text)
    if parsed is None:
        return []

    payloads = []
    if isinstance(parsed, dict):
        if "tool_calls" in parsed and isinstance(parsed["tool_calls"], list):
            for tool_call in parsed["tool_calls"]:
                function = tool_call.get("function") or {}
                payloads.append({
                    "name": function.get("name") or tool_call.get("name"),
                    "arguments": function.get("arguments"),
                    "call_id": tool_call.get("id") or tool_call.get("call_id"),
                })
        elif isinstance(parsed.get("function_call"), dict):
            function = parsed["function_call"]
            payloads.append({
                "name": function.get("name"),
                "arguments": function.get("arguments"),
                "call_id": function.get("id") or function.get("call_id"),
            })
        elif "name" in parsed and "arguments" in parsed:
            payloads.append(parsed)
    elif isinstance(parsed, list):
        for item in parsed:
            if not isinstance(item, dict):
                continue
            if "name" in item and "arguments" in item:
                payloads.append(item)
                continue
            function = item.get("function")
            if isinstance(function, dict):
                payloads.append({
                    "name": function.get("name") or item.get("name"),
                    "arguments": function.get("arguments"),
                    "call_id": item.get("id") or item.get("call_id"),
                })

    extracted = []
    for index, payload in enumerate(payloads, start=1):
        tool_name = (payload.get("name") or "").strip()
        if not tool_name:
            continue
        if tools and tool_name not in tools:
            continue

        arguments = payload.get("arguments")
        if isinstance(arguments, str):
            parsed_args = _parse_json_like_payload(arguments)
            arguments = parsed_args if isinstance(parsed_args, dict) else {}
        elif not isinstance(arguments, dict):
            arguments = {}

        tool_schema = (tools.get(tool_name) or (None, None, None, {}))[3] if tools else {}
        arguments = _autofill_required_tool_arguments(arguments, tool_schema, env_context)
        arguments = _coerce_tool_arguments_for_schema(arguments, tool_schema)

        call_id = payload.get("call_id") or payload.get("id") or f"era_call_{int(time.time() * 1000)}_{index}"
        extracted.append((tool_name, call_id, arguments))

    return extracted


def _normalize_tool_calls(tool_calls):
    """Force tool call entries to a stable (name, call_id, arguments) shape."""
    normalized = []
    for index, item in enumerate(tool_calls or [], start=1):
        tool_name = ""
        call_id = None
        arguments = {}

        if isinstance(item, (list, tuple)):
            if len(item) < 3:
                continue
            tool_name, call_id, arguments = item[0], item[1], item[2]
        elif isinstance(item, dict):
            tool_name = item.get("name") or item.get("tool")
            call_id = item.get("call_id") or item.get("id")
            arguments = item.get("arguments")
        else:
            continue

        tool_name = str(tool_name or "").strip()
        if not tool_name:
            continue
        if call_id in (None, ""):
            call_id = f"era_call_norm_{int(time.time() * 1000)}_{index}"
        if not isinstance(arguments, dict):
            arguments = {}
        normalized.append((tool_name, call_id, arguments))
    return normalized


def _patch_llm_api_service():
    if getattr(LLMApiService, "_era_custom_llm_patched", False):
        return

    original_init = LLMApiService.__init__
    original_get_api_token = LLMApiService._get_api_token
    original_get_base_headers = LLMApiService._get_base_headers
    original_get_embedding = LLMApiService.get_embedding
    original_request_llm_openai = LLMApiService._request_llm_openai
    original_request_llm_openai_helper = LLMApiService._request_llm_openai_helper
    original_request = LLMApiService._request
    original_request_llm = LLMApiService._request_llm
    original_to_open_ai_tool_schema = LLMApiService._to_open_ai_tool_schema
    original_build_tool_call_response = LLMApiService._build_tool_call_response
    openai_helper_return_arity = 3
    try:
        source = inspect.getsource(original_request_llm_openai)
        if "request_token_usage" in source:
            openai_helper_return_arity = 4
    except Exception:  # noqa: BLE001
        try:
            if "request_token_usage" in original_request_llm_openai.__code__.co_varnames:
                openai_helper_return_arity = 4
        except Exception:  # noqa: BLE001
            openai_helper_return_arity = 3

    def __init__(self, env, provider="openai"):
        if provider != "custom_llm":
            return original_init(self, env, provider)

        self.provider = provider
        self.env = env
        self.base_url = _custom_llm_param(env, "ai.custom_llm_base_url", "https://openrouter.ai/api/v1")

    def _get_api_token(self):
        if self.provider != "custom_llm":
            return original_get_api_token(self)

        api_key = (
            _custom_llm_param(self.env, "ai.custom_llm_key")
            or os.getenv("ODOO_AI_CUSTOM_LLM_TOKEN")
            or os.getenv("ODOO_AI_OPENROUTER_TOKEN")
        )
        if api_key:
            return api_key
        raise UserError(_("No API key set for provider '%s'", self.provider))

    def _get_base_headers(self):
        if self.provider != "custom_llm":
            return original_get_base_headers(self)

        auth_header = _custom_llm_param(self.env, "ai.custom_llm_auth_header", "Authorization")
        auth_prefix = _custom_llm_param(self.env, "ai.custom_llm_auth_prefix", "Bearer")
        token = self._get_api_token()

        auth_value = token if not auth_prefix else f"{auth_prefix} {token}"
        headers = {
            "Content-Type": "application/json",
            auth_header: auth_value,
        }
        if referer := _custom_llm_param(self.env, "ai.custom_llm_referer"):
            headers["HTTP-Referer"] = referer
        if title := _custom_llm_param(self.env, "ai.custom_llm_title"):
            headers["X-Title"] = title
        return headers

    def get_embedding(
        self,
        input,
        dimensions,
        model="text-embedding-3-small",
        encoding_format=None,
        user=None,
    ):
        model = _resolve_custom_model(self, model, is_embedding=True)
        if self.provider == "custom_llm" and not model:
            return None
        return original_get_embedding(
            self,
            input=input,
            dimensions=dimensions,
            model=model,
            encoding_format=encoding_format,
            user=user,
        )

    def _request(
        self, method, endpoint, headers, body,
        data=None, files=None, params=None,
        base_url=None, timeout=30
    ):
        if self.provider != "custom_llm":
            return original_request(
                self,
                method=method,
                endpoint=endpoint,
                headers=headers,
                body=body,
                data=data,
                files=files,
                params=params,
                base_url=base_url,
                timeout=timeout,
            )

        route = f"{base_url or self.base_url}/{endpoint.strip('/')}"
        retry_count = 2
        retry_delay = 1.0
        retry_override = getattr(self, "_era_http_retry_override", None)
        if retry_override is not None:
            retry_count = max(0, int(retry_override))
            retry_delay = 0.0
        try:
            if retry_override is None:
                retry_count = max(0, int(_custom_llm_param(self.env, "ai.custom_llm_retry_count", "2")))
        except (TypeError, ValueError):
            retry_count = 2
        try:
            if retry_override is None:
                retry_delay = max(0.0, float(_custom_llm_param(self.env, "ai.custom_llm_retry_delay", "1.0")))
        except (TypeError, ValueError):
            retry_delay = 1.0

        transient_statuses = {408, 409, 425, 429, 500, 502, 503, 504}
        last_error = None

        for attempt in range(retry_count + 1):
            try:
                response = requests.request(
                    method,
                    route,
                    params=params,
                    headers=headers,
                    json=body,
                    data=data,
                    timeout=timeout,
                    files=files,
                )
                response.raise_for_status()
                return response.json()
            except requests.exceptions.RequestException as e:
                last_error = e
                status = e.response.status_code if e.response is not None else None
                is_transient = status in transient_statuses or status is None
                if attempt < retry_count and is_transient:
                    if retry_delay:
                        time.sleep(retry_delay * (attempt + 1))
                    continue

        e = last_error
        if e is None:
            raise UserError(_("LLM provider request failed for an unknown reason."))

        try:
            status = e.response.status_code if e.response is not None else None
            detail = ""

            if e.response is not None:
                try:
                    parsed = e.response.json()
                    if isinstance(parsed, list) and parsed:
                        parsed = parsed[0]

                    if isinstance(parsed, dict):
                        err = parsed.get("error", parsed)
                        if isinstance(err, dict):
                            parts = []
                            if msg := (err.get("message") or err.get("detail") or err.get("error")):
                                parts.append(str(msg))
                            if err_type := err.get("type"):
                                parts.append(f"type={err_type}")
                            if code := err.get("code"):
                                parts.append(f"code={code}")
                            if param := err.get("param"):
                                parts.append(f"param={param}")
                            if meta := (err.get("metadata") or err.get("details")):
                                parts.append(f"metadata={json.dumps(meta, ensure_ascii=True)}")
                            detail = " | ".join(parts) if parts else json.dumps(parsed, ensure_ascii=True)
                        elif isinstance(err, str):
                            detail = err
                        else:
                            detail = json.dumps(parsed, ensure_ascii=True)
                    else:
                        detail = json.dumps(parsed, ensure_ascii=True)
                except ValueError:
                    detail = (e.response.text or "").strip()

            if not detail:
                detail = repr(e)

            # If provider gives an unhelpful generic message, append raw body preview.
            generic_markers = {"provider returned error", "unknown error", "request failed", "error"}
            if detail.strip().lower() in generic_markers and e.response is not None:
                raw_preview = (e.response.text or "").strip()
                if raw_preview:
                    detail = f"{detail} | raw={raw_preview[:500]}"

            if status:
                detail = f"HTTP {status}: {detail}"

            raise UserError(detail)
        except UserError:
            raise

    def _request_llm_openai(
        self, llm_model, system_prompts, user_prompts, tools=None,
        files=None, schema=None, temperature=0.2, inputs=(), web_grounding=False
    ):
        llm_model = _resolve_custom_model(self, llm_model, is_embedding=False)
        return original_request_llm_openai(
            self,
            llm_model=llm_model,
            system_prompts=system_prompts,
            user_prompts=user_prompts,
            tools=tools,
            files=files,
            schema=schema,
            temperature=temperature,
            inputs=inputs,
            web_grounding=web_grounding,
        )

    def _request_llm_openai_helper(self, body, tools=None, inputs=()):
        # gpt-5-family models are reasoning models that REJECT `temperature`.
        # The base only special-cases gpt-5 / gpt-5-mini when building the body,
        # so a newly-surfaced gpt-5-* (e.g. gpt-5-nano) would still carry it and
        # the API would 400. Strip it for any gpt-5* before the request is sent.
        if isinstance(body, dict) and (body.get("model") or "").startswith("gpt-5"):
            body.pop("temperature", None)
        if self.provider != "custom_llm":
            return original_request_llm_openai_helper(self, body, tools=tools, inputs=inputs)

        llm_response = self._request(
            method="post",
            endpoint="/responses",
            headers=self._get_base_headers(),
            body=body,
        )

        to_call = []
        response = []
        next_inputs = list(inputs or ())
        request_token_usage = llm_response.get("usage") or {}

        output_lines = llm_response.get("output") or []
        has_tool_calls = any(line.get("type") == "function_call" for line in output_lines)

        for line in output_lines:
            if line.get("type") == "function_call":
                tool_name = line.get("name", "")
                try:
                    arguments = json.loads(line.get("arguments") or "")
                except json.decoder.JSONDecodeError:
                    arguments = {}
                tool_schema = (tools.get(tool_name) or (None, None, None, {}))[3] if tools else {}
                arguments = _autofill_required_tool_arguments(arguments, tool_schema, self.env.context)
                arguments = _coerce_tool_arguments_for_schema(arguments, tool_schema)
                to_call.append((tool_name, line.get("call_id"), arguments))
                next_inputs.append(line)
                continue

            if text := line.get("text"):
                parsed_tool_calls = _extract_tool_calls_from_text(text, tools, self.env.context)
                if parsed_tool_calls:
                    has_tool_calls = True
                    for tool_name, call_id, arguments in parsed_tool_calls:
                        to_call.append((tool_name, call_id, arguments))
                        next_inputs.append({
                            "type": "function_call",
                            "name": tool_name,
                            "call_id": call_id,
                            "arguments": json.dumps(arguments, ensure_ascii=False),
                        })
                    continue
                if has_tool_calls:
                    continue
                response.append(text)
                continue

            if line.get("type") == "message":
                content = line.get("content")
                if isinstance(content, str):
                    parsed_tool_calls = _extract_tool_calls_from_text(content, tools, self.env.context)
                    if parsed_tool_calls:
                        has_tool_calls = True
                        for tool_name, call_id, arguments in parsed_tool_calls:
                            to_call.append((tool_name, call_id, arguments))
                            next_inputs.append({
                                "type": "function_call",
                                "name": tool_name,
                                "call_id": call_id,
                                "arguments": json.dumps(arguments, ensure_ascii=False),
                            })
                        continue
                    if has_tool_calls:
                        continue
                    response.append(content)
                elif isinstance(content, list):
                    for item in content:
                        if text := item.get("text"):
                            parsed_tool_calls = _extract_tool_calls_from_text(text, tools, self.env.context)
                            if parsed_tool_calls:
                                has_tool_calls = True
                                for tool_name, call_id, arguments in parsed_tool_calls:
                                    to_call.append((tool_name, call_id, arguments))
                                    next_inputs.append({
                                        "type": "function_call",
                                        "name": tool_name,
                                        "call_id": call_id,
                                        "arguments": json.dumps(arguments, ensure_ascii=False),
                                    })
                                continue
                            if has_tool_calls:
                                continue
                            response.append(text)
                        elif item.get("type") == "output_text" and item.get("text"):
                            output_text = item["text"]
                            parsed_tool_calls = _extract_tool_calls_from_text(output_text, tools, self.env.context)
                            if parsed_tool_calls:
                                has_tool_calls = True
                                for tool_name, call_id, arguments in parsed_tool_calls:
                                    to_call.append((tool_name, call_id, arguments))
                                    next_inputs.append({
                                        "type": "function_call",
                                        "name": tool_name,
                                        "call_id": call_id,
                                        "arguments": json.dumps(arguments, ensure_ascii=False),
                                    })
                                continue
                            if has_tool_calls:
                                continue
                            response.append(output_text)

        if not response and isinstance(llm_response.get("output_text"), str):
            output_text = llm_response["output_text"]
            parsed_tool_calls = _extract_tool_calls_from_text(output_text, tools, self.env.context)
            if parsed_tool_calls:
                has_tool_calls = True
                for tool_name, call_id, arguments in parsed_tool_calls:
                    to_call.append((tool_name, call_id, arguments))
                    next_inputs.append({
                        "type": "function_call",
                        "name": tool_name,
                        "call_id": call_id,
                        "arguments": json.dumps(arguments, ensure_ascii=False),
                    })
            else:
                response.append(output_text)

        # Some providers return chat-completions shape even on compatibility routes.
        choices = llm_response.get("choices") or []
        if not response and choices:
            for choice in choices:
                message = choice.get("message") or {}
                tool_calls = message.get("tool_calls") or []
                if tool_calls:
                    for tc in tool_calls:
                        function = tc.get("function") or {}
                        name = function.get("name", "")
                        call_id = tc.get("id") or name
                        try:
                            arguments = json.loads(function.get("arguments") or "{}")
                        except json.decoder.JSONDecodeError:
                            arguments = {}
                        tool_schema = (tools.get(name) or (None, None, None, {}))[3] if tools else {}
                        arguments = _autofill_required_tool_arguments(arguments, tool_schema, self.env.context)
                        arguments = _coerce_tool_arguments_for_schema(arguments, tool_schema)
                        to_call.append((name, call_id, arguments))
                elif message.get("content"):
                    content_text = message["content"]
                    parsed_tool_calls = _extract_tool_calls_from_text(content_text, tools, self.env.context)
                    if parsed_tool_calls:
                        has_tool_calls = True
                        for tool_name, call_id, arguments in parsed_tool_calls:
                            to_call.append((tool_name, call_id, arguments))
                            next_inputs.append({
                                "type": "function_call",
                                "name": tool_name,
                                "call_id": call_id,
                                "arguments": json.dumps(arguments, ensure_ascii=False),
                            })
                    else:
                        response.append(content_text)

        to_call = _normalize_tool_calls(to_call)
        if openai_helper_return_arity >= 4:
            return response, to_call, next_inputs, request_token_usage
        return response, to_call, next_inputs

    def _request_llm(self, *args, **kwargs):
        if self.provider == "custom_llm":
            requested_model = kwargs.get("llm_model")
            if requested_model is None and args:
                requested_model = args[0]

            if requested_model == CUSTOM_LLM_MODEL_KEY or not requested_model:
                models_to_try = _custom_llm_retry_models(self.env)
            else:
                models_to_try = [_resolve_custom_model(self, requested_model, is_embedding=False)]

            if not models_to_try:
                raise UserError(_("No custom LLM model is configured."))

            last_error = None
            tried_models = []
            for model in models_to_try:
                tried_models.append(model)
                try:
                    # In model-fallback mode, switch model per attempt
                    # instead of retrying the same model repeatedly.
                    self._era_http_retry_override = 0
                    if args:
                        new_args = list(args)
                        new_args[0] = model
                        new_kwargs = dict(kwargs)
                        new_kwargs.pop("llm_model", None)
                        result = self._request_llm_openai(*tuple(new_args), **new_kwargs)
                        self._era_http_retry_override = None
                        return result

                    new_kwargs = dict(kwargs)
                    new_kwargs["llm_model"] = model
                    result = self._request_llm_openai(**new_kwargs)
                    self._era_http_retry_override = None
                    return result
                except Exception as error:  # noqa: BLE001
                    last_error = error
                    self._era_http_retry_override = None
                    continue

            if last_error:
                raise UserError(
                    _("All configured models failed (%(models)s): %(error)s",
                      models=", ".join(tried_models),
                      error=str(last_error))
                )
            raise UserError(_("LLM request failed."))
        return original_request_llm(self, *args, **kwargs)

    def _to_open_ai_tool_schema(self, schema):
        if self.provider == "custom_llm":
            required_key = "required"
            for tool in schema:
                # Some OpenAI-compatible gateways reject strict=true in tools payload.
                # Keep compatibility by explicitly disabling strict mode for custom providers.
                if "strict" in tool:
                    tool["strict"] = False
                required = tool["parameters"].get(required_key, [])
                non_required = set(tool["parameters"]["properties"]) - set(required)
                for name in non_required:
                    field_type = tool["parameters"]["properties"][name]["type"]
                    if not isinstance(field_type, list):
                        tool["parameters"]["properties"][name]["type"] = [field_type, "null"]
                tool["parameters"].setdefault(required_key, []).extend(non_required)
                tool["parameters"]["additionalProperties"] = False
            return schema
        return original_to_open_ai_tool_schema(self, schema)

    def _build_tool_call_response(self, tool_call_id, return_value):
        if self.provider == "custom_llm":
            return {
                "type": "function_call_output",
                "call_id": tool_call_id,
                "output": str(return_value),
            }
        return original_build_tool_call_response(self, tool_call_id, return_value)

    LLMApiService.__init__ = __init__
    LLMApiService._get_api_token = _get_api_token
    LLMApiService._get_base_headers = _get_base_headers
    LLMApiService.get_embedding = get_embedding
    LLMApiService._request = _request
    LLMApiService._request_llm_openai = _request_llm_openai
    LLMApiService._request_llm_openai_helper = _request_llm_openai_helper
    LLMApiService._request_llm = _request_llm
    LLMApiService._to_open_ai_tool_schema = _to_open_ai_tool_schema
    LLMApiService._build_tool_call_response = _build_tool_call_response
    LLMApiService._era_custom_llm_patched = True


_patch_llm_api_service()

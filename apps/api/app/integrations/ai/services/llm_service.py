import json
import time
from dataclasses import dataclass

import httpx

from app.core.config import get_settings
from app.integrations.ai.services.model_call_log_service import record_model_call
from app.shared.schemas.common import AppError, ErrorCode


PROVIDERS = {
    "deepseek": ("https://api.deepseek.com/v1", "deepseek_api_key"),
    "openai": ("https://api.openai.com/v1", "openai_api_key"),
    "dashscope": (
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "dashscope_api_key",
    ),
    "volcengine": (
        "https://ark.cn-beijing.volces.com/api/v3",
        "volcengine_api_key",
    ),
    "ark": (
        "https://ark.cn-beijing.volces.com/api/v3",
        "ark_api_key",
    ),
}


@dataclass(frozen=True)
class ModelConfig:
    provider: str
    model: str


def get_model_config(purpose: str) -> ModelConfig:
    settings = get_settings()
    provider, model = _resolve_model_config(settings, purpose)
    return ModelConfig(provider=provider, model=model)


async def generate_answer(
    prompt: str,
    purpose: str = "rag",
    *,
    model_override: str | None = None,
    provider_override: str | None = None,
    shadow: bool = False,
    prompt_version: str | None = None,
) -> dict:
    config = _model_config(
        purpose,
        model_override=model_override,
        provider_override=provider_override,
    )
    if config.provider == "mock":
        answer = _mock_answer(prompt)
        return {
            "answer": answer,
            "usage": {
                "prompt_tokens": len(prompt),
                "completion_tokens": len(answer),
            },
        }
    body = await _chat_completion(
        config=config,
        purpose=purpose,
        prompt=prompt,
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
        shadow=shadow,
        prompt_version=prompt_version,
    )
    return {
        "answer": body["choices"][0]["message"]["content"],
        "usage": body.get("usage", {}),
    }


async def generate_messages(
    messages: list[dict[str, str]],
    *,
    purpose: str,
    temperature: float = 0,
    model_override: str | None = None,
    provider_override: str | None = None,
    shadow: bool = False,
    prompt_version: str | None = None,
) -> dict:
    """Generate from role-separated messages without flattening system instructions."""
    config = _model_config(
        purpose,
        model_override=model_override,
        provider_override=provider_override,
    )
    if config.provider == "mock":
        return {"answer": "", "usage": {}}
    prompt = json.dumps(messages, ensure_ascii=False, separators=(",", ":"))
    attempts = 2 if purpose == "persona" else 1
    for attempt in range(1, attempts + 1):
        try:
            body = await _chat_completion(
                config=config,
                purpose=purpose,
                prompt=prompt,
                messages=messages,
                temperature=temperature,
                shadow=shadow,
                prompt_version=prompt_version,
                attempt=attempt,
            )
            break
        except AppError as exc:
            if attempt == attempts or not isinstance(
                exc.__cause__,
                (httpx.TimeoutException, httpx.NetworkError),
            ):
                raise
    return {
        "answer": body["choices"][0]["message"]["content"],
        "usage": body.get("usage", {}),
    }


async def generate_messages_json(
    messages: list[dict[str, str]],
    *,
    purpose: str,
    temperature: float = 0,
    prompt_version: str | None = None,
) -> dict:
    """Generate one JSON object from role-separated Agent messages."""
    config = _model_config(
        purpose,
        model_override=None,
        provider_override=None,
    )
    if config.provider == "mock":
        return {
            "data": {
                "commercial_judgment": "当前缺少真实模型配置，先保持自然承接",
                "relationship_purpose": "继续了解客户当前最重要的问题",
                "customer_signal": "none",
                "tool_calls": [],
                "final_response": {
                    "messages": [
                        {
                            "type": "text",
                            "content": "您接着说就行，我先按您现在最想解决的问题帮您看。",
                        }
                    ],
                    "need_human": False,
                },
            },
            "usage": {},
        }
    prompt = json.dumps(messages, ensure_ascii=False, separators=(",", ":"))
    last_error: Exception | None = None
    for attempt in range(1, 3):
        try:
            body = await _chat_completion(
                config=config,
                purpose=purpose,
                prompt=prompt,
                messages=messages,
                temperature=temperature,
                shadow=False,
                prompt_version=prompt_version,
                attempt=attempt,
                json_mode=True,
            )
            return {
                "data": json.loads(body["choices"][0]["message"]["content"]),
                "usage": body.get("usage", {}),
            }
        except json.JSONDecodeError as exc:
            last_error = exc
    raise AppError(ErrorCode.MODEL_SCHEMA_INVALID) from last_error


async def generate_json(
    prompt: str,
    purpose: str = "profile",
    *,
    model_override: str | None = None,
    provider_override: str | None = None,
    shadow: bool = False,
    prompt_version: str | None = None,
) -> dict:
    config = _model_config(
        purpose,
        model_override=model_override,
        provider_override=provider_override,
    )
    if config.provider == "mock":
        return {}

    last_error: Exception | None = None
    for attempt in range(1, 3):
        try:
            body = await _chat_completion(
                config=config,
                purpose=purpose,
                prompt=prompt,
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                shadow=shadow,
                prompt_version=prompt_version,
                attempt=attempt,
                json_mode=True,
            )
            return json.loads(body["choices"][0]["message"]["content"])
        except json.JSONDecodeError as exc:
            last_error = exc
    raise AppError(ErrorCode.MODEL_SCHEMA_INVALID) from last_error


async def _chat_completion(
    *,
    config: ModelConfig,
    purpose: str,
    prompt: str,
    messages: list[dict[str, str]],
    temperature: float,
    shadow: bool,
    prompt_version: str | None,
    attempt: int = 1,
    json_mode: bool = False,
) -> dict:
    provider = config.provider
    if provider not in PROVIDERS:
        raise AppError(
            ErrorCode.LLM_FAILED,
            f"Unsupported {purpose} LLM provider: {provider}",
            status_code=500,
        )

    settings = get_settings()
    base_url, key_name = PROVIDERS[provider]
    request_body = {
        "model": config.model,
        "messages": messages,
        "temperature": temperature,
    }
    deepseek_v4 = config.model.lower().startswith("deepseek-v4-")
    if deepseek_v4 and purpose == "review":
        request_body["reasoning_effort"] = settings.review_llm_reasoning_effort
    if purpose == "persona":
        request_body["max_tokens"] = 300
    if provider == "dashscope" and purpose in {"persona", "rag_fast", "business"}:
        request_body["enable_thinking"] = False
    if json_mode and provider == "dashscope":
        request_body["enable_thinking"] = deepseek_v4
        request_body["response_format"] = {"type": "json_object"}

    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=_timeout_for(settings, purpose)) as client:
            response = await client.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {getattr(settings, key_name)}"},
                json=request_body,
            )
            response.raise_for_status()
            body = response.json()
            if json_mode:
                json.loads(body["choices"][0]["message"]["content"])
        _record_success(
            purpose=purpose,
            config=config,
            prompt=prompt,
            duration_ms=_elapsed_ms(started),
            attempt=attempt,
            body=body,
            response=response,
            shadow=shadow,
            prompt_version=prompt_version,
        )
        return body
    except json.JSONDecodeError as exc:
        record_model_call(
            purpose=purpose,
            provider=provider,
            model=config.model,
            prompt=prompt,
            duration_ms=_elapsed_ms(started),
            attempt=attempt,
            status="invalid_json",
            error_class=type(exc).__name__,
            shadow=shadow,
            prompt_version=prompt_version,
        )
        raise
    except Exception as exc:
        record_model_call(
            purpose=purpose,
            provider=provider,
            model=config.model,
            prompt=prompt,
            duration_ms=_elapsed_ms(started),
            attempt=attempt,
            status="failed",
            error_class=type(exc).__name__,
            shadow=shadow,
            prompt_version=prompt_version,
        )
        if isinstance(exc, AppError):
            raise
        raise AppError(ErrorCode.LLM_FAILED, status_code=502) from exc


def _record_success(
    *,
    purpose: str,
    config: ModelConfig,
    prompt: str,
    duration_ms: int,
    attempt: int,
    body: dict,
    response: httpx.Response,
    shadow: bool,
    prompt_version: str | None,
) -> None:
    usage = body.get("usage") if isinstance(body, dict) else {}
    usage = usage if isinstance(usage, dict) else {}
    record_model_call(
        purpose=purpose,
        provider=config.provider,
        model=config.model,
        prompt=prompt,
        duration_ms=duration_ms,
        attempt=attempt,
        status="success",
        input_tokens=_as_int(usage.get("prompt_tokens") or usage.get("input_tokens")),
        output_tokens=_as_int(
            usage.get("completion_tokens") or usage.get("output_tokens")
        ),
        provider_request_id=str(
            body.get("request_id")
            or body.get("id")
            or getattr(response, "headers", {}).get("x-request-id")
            or ""
        ),
        shadow=shadow,
        prompt_version=prompt_version,
    )


def _model_config(
    purpose: str,
    *,
    model_override: str | None,
    provider_override: str | None,
) -> ModelConfig:
    default = get_model_config(purpose)
    return ModelConfig(
        provider=(provider_override or default.provider).lower(),
        model=model_override or default.model,
    )


def _timeout_for(settings, purpose: str) -> float:
    if purpose == "persona":
        return settings.persona_llm_timeout_seconds
    if purpose in {"rag", "rag_fast", "business"}:
        return settings.rag_llm_timeout_seconds
    return settings.llm_timeout_seconds


def _resolve_model_config(settings, purpose: str) -> tuple[str, str]:
    purpose = purpose.lower()
    chains = {
        "rag": (("rag_llm_provider", "rag_llm_model"),),
        "rag_fast": (
            ("rag_fast_llm_provider", "rag_fast_llm_model"),
            ("rag_llm_provider", "rag_llm_model"),
        ),
        "business": (
            ("business_llm_provider", "business_llm_model"),
            ("rag_llm_provider", "rag_llm_model"),
        ),
        "persona": (
            ("persona_llm_provider", "persona_llm_model"),
            ("rag_llm_provider", "rag_llm_model"),
        ),
        "profile": (
            ("profile_llm_provider", "profile_llm_model"),
        ),
        "review": (("review_llm_provider", "review_llm_model"),),
    }
    for provider_name, model_name in chains.get(purpose, ()):
        provider = str(getattr(settings, provider_name, "") or "").strip()
        model = str(getattr(settings, model_name, "") or "").strip()
        if provider or model:
            return (provider or settings.llm_provider).lower(), model or settings.llm_model
    return settings.llm_provider.lower(), settings.llm_model


def _mock_answer(prompt: str) -> str:
    context = prompt
    for start, end in (
        ("\n## 参考资料\n", "\n## 用户问题\n"),
        ("\n【知识库资料】\n", "\n【用户问题】\n"),
    ):
        if start in prompt:
            context = prompt.split(start, 1)[-1].split(end, 1)[0]
            break
    return next(
        (
            line.strip()
            for line in context.splitlines()
            if line.strip() and not line.strip().startswith("[")
        ),
        "__HANDOFF__",
    )


def _elapsed_ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


def _as_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None

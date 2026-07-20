import json
from dataclasses import dataclass

import httpx

from app.config import get_settings
from app.schemas.common import AppError, ErrorCode


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


async def generate_answer(prompt: str, purpose: str = "rag") -> dict:
    config = get_model_config(purpose)
    provider = config.provider
    if provider == "mock":
        if "\n## 参考资料\n" in prompt:
            context = prompt.split("\n## 参考资料\n", 1)[-1].split(
                "\n## 用户问题\n", 1
            )[0]
        elif "\n【知识库资料】\n" in prompt:
            context = prompt.split("\n【知识库资料】\n", 1)[-1].split(
                "\n【用户问题】\n", 1
            )[0]
        else:
            context = prompt.rsplit("【知识库资料】", 1)[-1].split(
                "【用户问题】", 1
            )[0]
        first_line = next(
            (
                line.strip()
                for line in context.splitlines()
                if line.strip() and not line.strip().startswith("[")
            ),
            "__HANDOFF__",
        )
        return {
            "answer": first_line,
            "usage": {"prompt_tokens": len(prompt), "completion_tokens": len(first_line)},
        }

    if provider not in PROVIDERS:
        raise AppError(
            ErrorCode.LLM_FAILED,
            f"不支持的 {purpose} LLM Provider: {provider}",
            status_code=500,
        )

    base_url, key_name = PROVIDERS[provider]
    settings = get_settings()
    api_key = getattr(settings, key_name)
    try:
        async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
            response = await client.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": config.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0,
                },
            )
            response.raise_for_status()
            body = response.json()
            return {
                "answer": body["choices"][0]["message"]["content"],
                "usage": body.get("usage", {}),
            }
    except Exception as exc:
        raise AppError(ErrorCode.LLM_FAILED, status_code=502) from exc


async def generate_json(prompt: str, purpose: str = "intent") -> dict:
    config = get_model_config(purpose)
    provider = config.provider
    if provider == "mock":
        return {
            "route": "clarify",
            "primary_intent": "unknown",
            "confidence": 0.5,
            "reason": "mock_llm_json",
        }

    if provider not in PROVIDERS:
        raise AppError(
            ErrorCode.LLM_FAILED,
            f"不支持的 {purpose} LLM Provider: {provider}",
            status_code=500,
        )

    base_url, key_name = PROVIDERS[provider]
    settings = get_settings()
    api_key = getattr(settings, key_name)
    last_error: Exception | None = None
    for _ in range(2):
        try:
            async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
                response = await client.post(
                    f"{base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={
                        "model": config.model,
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0,
                    },
                )
                response.raise_for_status()
                body = response.json()
                content = body["choices"][0]["message"]["content"]
                return json.loads(content)
        except json.JSONDecodeError as exc:
            last_error = exc
        except Exception as exc:
            raise AppError(ErrorCode.LLM_FAILED, status_code=502) from exc
    raise AppError(ErrorCode.INTENT_SCHEMA_INVALID) from last_error


async def classify_intent(prompt: str) -> dict:
    return await generate_json(prompt, purpose="intent")


def _resolve_model_config(settings, purpose: str) -> tuple[str, str]:
    purpose = purpose.lower()
    chains = {
        "rag": (("rag_llm_provider", "rag_llm_model"),),
        "business": (
            ("business_llm_provider", "business_llm_model"),
            ("rag_llm_provider", "rag_llm_model"),
        ),
        "intent": (("intent_llm_provider", "intent_llm_model"),),
        "talk_script": (
            ("talk_script_llm_provider", "talk_script_llm_model"),
            ("intent_llm_provider", "intent_llm_model"),
        ),
        "profile": (
            ("profile_llm_provider", "profile_llm_model"),
            ("intent_llm_provider", "intent_llm_model"),
        ),
        "review": (("review_llm_provider", "review_llm_model"),),
    }
    for provider_name, model_name in chains.get(purpose, ()):
        provider = str(getattr(settings, provider_name, "") or "").strip()
        model = str(getattr(settings, model_name, "") or "").strip()
        if provider:
            return provider.lower(), model or settings.llm_model
    return settings.llm_provider.lower(), settings.llm_model

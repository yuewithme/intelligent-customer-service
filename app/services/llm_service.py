import json

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


async def generate_answer(prompt: str) -> dict:
    settings = get_settings()
    provider = settings.llm_provider.lower()
    if provider == "mock":
        context = prompt.rsplit("【知识库资料】", 1)[-1].split(
            "【用户问题】", 1
        )[0]
        first_line = next(
            (
                line.strip()
                for line in context.splitlines()
                if line.strip() and not line.strip().startswith("[")
            ),
            "知识库中没有找到明确答案。",
        )
        return {
            "answer": first_line,
            "usage": {"prompt_tokens": len(prompt), "completion_tokens": len(first_line)},
        }

    if provider not in PROVIDERS:
        raise AppError(
            ErrorCode.LLM_FAILED,
            f"不支持的 LLM Provider: {settings.llm_provider}",
            status_code=500,
        )

    base_url, key_name = PROVIDERS[provider]
    api_key = getattr(settings, key_name)
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": settings.llm_model,
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


async def generate_json(prompt: str) -> dict:
    settings = get_settings()
    provider = settings.intent_llm_provider.lower()
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
            f"不支持的意图 LLM Provider: {settings.intent_llm_provider}",
            status_code=500,
        )

    base_url, key_name = PROVIDERS[provider]
    api_key = getattr(settings, key_name)
    last_error: Exception | None = None
    for _ in range(2):
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.post(
                    f"{base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={
                        "model": settings.intent_llm_model,
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
    return await generate_json(prompt)

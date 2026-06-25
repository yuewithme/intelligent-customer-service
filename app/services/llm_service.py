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

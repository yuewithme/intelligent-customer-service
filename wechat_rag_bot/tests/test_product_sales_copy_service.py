import json

import pytest

from app.services.product_sales_copy_service import (
    build_product_sales_copy_prompt,
    generate_product_sales_copy,
    parse_sales_copy_response,
    validate_sales_copy,
)


PRODUCT = {
    "product_name": "芽黄素",
    "category": "建兰",
    "flower_color": "黄绿色",
    "fragrance": "清香",
    "bloom_period": "6月-11月",
    "care_scenes": "阳台,室内",
    "audience_tag": "L3",
    "highlighted_features": "新芽由淡黄色逐渐转为黄绿色，花朵为淡黄色素花。",
    "sales_copy": "旧话术",
}


def test_sales_copy_prompt_is_for_product_shaping_not_field_listing():
    prompt = build_product_sales_copy_prompt(PRODUCT)

    assert "为什么值得被客户记住" in prompt
    assert "开头不要重复商品名称" in prompt
    assert "最终话术中禁止出现L1—L6" in prompt
    assert '"商品名称": "芽黄素"' in prompt
    assert "旧话术" not in prompt


def test_parse_and_validate_sales_copy_json():
    copy = parse_sales_copy_response(
        '```json\n{"sales_copy":"淡黄色的新芽会随着生长慢慢沉成黄绿色，尚未开花便有鲜明的观赏层次。待淡黄色素花舒展，花叶之间的色调自然呼应，整体清雅而不张扬。它更适合放在日常能够细看的位置，让每一次生长变化都成为养兰过程中的小惊喜。"}\n```'
    )

    assert validate_sales_copy(copy, PRODUCT) == []


def test_validate_sales_copy_rejects_transaction_and_internal_level():
    errors = validate_sales_copy("L3唯一名品，当前68元，立即下单。", PRODUCT)

    assert "包含内部等级" in errors
    assert "包含价格" in errors
    assert "包含实时交易信息" in errors
    assert "无资料依据的表达：唯一" in errors


def test_validate_sales_copy_rejects_old_copy_only_historical_claim():
    product = {
        **PRODUCT,
        "sales_copy": "清代名品，印在人民币上的兰花",
    }
    copy = (
        "翠绿的荷瓣与雪白素心自然呼应，整体显得端庄而纯净，初春绽放时尤其清雅。"
        "作为印在人民币上的清代名品，它把传统荷瓣的规整与素心的洁净融在一处。"
        "摆在阳台或书房细细观赏，更能感受其中沉静而耐看的东方韵味。"
    )

    errors = validate_sales_copy(copy, product)

    assert "无资料依据的表达：人民币" in errors
    assert "无资料依据的表达：清代" in errors


@pytest.mark.asyncio
async def test_generate_sales_copy_retries_invalid_response(monkeypatch):
    responses = iter(
        [
            {"answer": '{"sales_copy":"太短"}'},
            {
                "answer": json.dumps(
                    {"sales_copy": (
                    "淡黄色的新芽会随着生长慢慢沉成黄绿色，尚未开花便有鲜明的观赏层次。"
                    "待淡黄色素花舒展，花叶之间的色调自然呼应，整体清雅而不张扬。"
                    "放在日常能够细看的位置，每一段生长变化都会让养护过程多一层耐人寻味的乐趣。"
                    )},
                    ensure_ascii=False,
                ),
            },
        ]
    )

    async def fake_generate_answer(prompt, purpose):
        assert purpose == "rag"
        return next(responses)

    monkeypatch.setattr(
        "app.services.product_sales_copy_service.generate_answer",
        fake_generate_answer,
    )

    copy = await generate_product_sales_copy(PRODUCT)

    assert len(copy) >= 90


@pytest.mark.asyncio
async def test_generate_sales_copy_retries_transient_provider_failure(monkeypatch):
    valid = (
        "淡黄色的新芽会随着生长慢慢沉成黄绿色，尚未开花便有鲜明的观赏层次。"
        "待淡黄色素花舒展，花叶之间的色调自然呼应，整体清雅而不张扬。"
        "放在日常能够细看的位置，每一段变化都会让养护过程多一层耐人寻味的乐趣。"
    )
    responses = iter([RuntimeError("timeout"), {"answer": json.dumps({"sales_copy": valid}, ensure_ascii=False)}])

    async def fake_generate_answer(prompt, purpose):
        result = next(responses)
        if isinstance(result, Exception):
            raise result
        return result

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr("app.services.product_sales_copy_service.generate_answer", fake_generate_answer)
    monkeypatch.setattr("app.services.product_sales_copy_service.asyncio.sleep", no_sleep)

    assert await generate_product_sales_copy(PRODUCT) == valid

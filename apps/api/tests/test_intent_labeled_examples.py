import pytest

from app.core.config import get_settings
from app.domains.decisioning.services import (
    intent_example_service,
    intent_observation_service,
)
from app.domains.decisioning.services.intent_taxonomy_service import (
    format_candidate_cards_compact,
)


@pytest.fixture(autouse=True)
def _reset_example_cache():
    intent_example_service._labeled_example_cache.clear()
    yield
    intent_example_service._labeled_example_cache.clear()
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_retrieves_only_normalized_trusted_examples_with_context(
    monkeypatch, tmp_path
):
    monkeypatch.setenv(
        "CHAT_LOG_DB_URL", f"sqlite:///{(tmp_path / 'examples.db').as_posix()}"
    )
    get_settings.cache_clear()

    async def fake_training_dataset(**_kwargs):
        return [
            {
                "sample_id": "corrected-price",
                "text": "这个价格有点贵，我再考虑一下",
                "context": [
                    {"role": "assistant", "content": "这款目前活动价是 399 元"}
                ],
                "labels": {
                    "primary_domain": "commerce",
                    "primary_goal": "express_objection",
                    "issues": ["price_value"],
                    "scope": "in_scope",
                },
                "annotation": {"status": "corrected", "origin": "human"},
            },
            {
                "sample_id": "invalid-label",
                "text": "这个价格有点贵",
                "context": [],
                "labels": {
                    "primary_domain": "not-a-domain",
                    "primary_goal": "express_objection",
                    "issues": [],
                },
                "annotation": {"status": "confirmed", "origin": "human"},
            },
            {
                "sample_id": "corrected-but-irrelevant",
                "text": "我来查一下",
                "context": [
                    {"role": "assistant", "content": "您是觉得价格有点贵，需要再考虑吗"}
                ],
                "labels": {
                    "primary_domain": "commerce",
                    "primary_goal": "ask_information",
                    "issues": ["price_value"],
                    "scope": "in_scope",
                },
                "annotation": {"status": "corrected", "origin": "human"},
            },
        ]

    monkeypatch.setattr(
        intent_observation_service, "build_training_dataset", fake_training_dataset
    )

    matches = await intent_example_service._retrieve_labeled_examples(
        "价格有点贵，先考虑"
    )

    assert [item["example_id"] for item in matches] == ["corrected-price"]
    assert matches[0]["primary_domain"] == "commerce"
    assert matches[0]["primary_goal"] == "express_objection"
    assert matches[0]["issues"] == ["price_value"]
    assert matches[0]["context"][0]["role"] == "assistant"


@pytest.mark.asyncio
async def test_training_examples_are_cached(monkeypatch, tmp_path):
    monkeypatch.setenv(
        "CHAT_LOG_DB_URL", f"sqlite:///{(tmp_path / 'cache.db').as_posix()}"
    )
    get_settings.cache_clear()
    calls = 0

    async def fake_training_dataset(**_kwargs):
        nonlocal calls
        calls += 1
        return []

    monkeypatch.setattr(
        intent_observation_service, "build_training_dataset", fake_training_dataset
    )

    await intent_example_service._trusted_training_samples()
    await intent_example_service._trusted_training_samples()

    assert calls == 1


def test_compact_cards_include_labeled_dgi_example_and_context():
    text = format_candidate_cards_compact(
        [
            {
                "kind": "domain",
                "id": "commerce",
                "definition": "购买、价格与交易",
            },
            {
                "kind": "goal",
                "id": "express_objection",
                "definition": "明确表达顾虑或异议",
            },
            {
                "kind": "issue",
                "id": "price_value",
                "definition": "价格与价值顾虑",
            },
            {
                "kind": "example",
                "text": "有点贵，我再想想",
                "context": [{"role": "assistant", "content": "活动价 399 元"}],
                "primary_domain": "commerce",
                "primary_goal": "express_objection",
                "issues": ["price_value"],
            },
        ]
    )

    assert "trusted labeled examples" in text
    assert "assistant: 活动价 399 元" in text
    assert "D=commerce" in text
    assert "G=express_objection" in text
    assert "I=price_value" in text

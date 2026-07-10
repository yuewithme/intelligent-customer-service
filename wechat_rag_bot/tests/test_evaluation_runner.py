import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from evaluation.run_evaluation import (
    aggregate_scores,
    append_jsonl,
    build_single_message,
    choose_pending_items,
    judge_result,
    load_jsonl,
    parse_judge_json,
    run_chat_stage,
    select_run_stages,
)
from evaluation.retry_evaluation import merge_by_id


def test_build_single_message_wraps_context_without_exposing_rubric():
    item = {
        "conversation": [
            {"role": "system", "content": "客户在甘肃天水，养建兰。"},
            {"role": "user", "content": "去年全部养死了。"},
        ],
        "must_have": ["不应出现在输入中"],
    }

    message = build_single_message(item)

    assert "【已知对话背景】" in message
    assert "客户在甘肃天水，养建兰。" in message
    assert "【客户当前消息】" in message
    assert message.endswith("去年全部养死了。")
    assert "不应出现在输入中" not in message


def test_parse_judge_json_accepts_markdown_fence():
    payload = """```json
{"id":"item_1","score":82,"critical_error":false}
```"""

    result = parse_judge_json(payload)

    assert result == {"id": "item_1", "score": 82, "critical_error": False}


def test_aggregate_scores_groups_by_subset_and_capability():
    rows = [
        {
            "subset": "single_turn",
            "primary_capability": "Q2",
            "score": 80,
            "critical_error": False,
        },
        {
            "subset": "single_turn",
            "primary_capability": "Q2",
            "score": 60,
            "critical_error": True,
        },
        {
            "subset": "multi_turn",
            "primary_capability": "MULTI",
            "score": 90,
            "critical_error": False,
        },
    ]

    report = aggregate_scores(rows)

    assert report["overall"]["count"] == 3
    assert report["overall"]["average_score"] == 76.67
    assert report["overall"]["critical_error_rate"] == 0.3333
    assert report["by_subset"]["single_turn"]["average_score"] == 70
    assert report["by_capability"]["Q2"]["count"] == 2


def test_merge_by_id_replaces_only_matching_rows():
    original = [{"id": "a", "value": 1}, {"id": "b", "value": 2}]
    replacements = [{"id": "b", "value": 3}]

    assert merge_by_id(original, replacements) == [
        {"id": "a", "value": 1},
        {"id": "b", "value": 3},
    ]


def test_choose_pending_items_skips_completed_rows_and_retries_failed_rows():
    items = [{"id": "done"}, {"id": "failed"}, {"id": "new"}]
    existing = {
        "done": {"id": "done", "error": None},
        "failed": {"id": "failed", "error": "timeout"},
    }

    assert choose_pending_items(items, existing) == [
        {"id": "failed"},
        {"id": "new"},
    ]


def test_append_jsonl_preserves_prior_rows_for_interrupted_evaluation(tmp_path: Path):
    path = tmp_path / "raw_responses.jsonl"

    append_jsonl(path, {"id": "first"})
    append_jsonl(path, {"id": "second"})

    assert [json.loads(line)["id"] for line in path.read_text(encoding="utf-8").splitlines()] == [
        "first",
        "second",
    ]


def test_judge_only_mode_disables_chat_stage():
    assert select_run_stages(chat_only=False, judge_only=True) == (False, True)


@pytest.mark.asyncio
async def test_completed_chat_is_appended_before_next_chat_finishes(tmp_path: Path):
    release = asyncio.Event()

    class FakeRunner:
        async def run_single(self, client, item):
            del client
            if item["id"] == "slow":
                await release.wait()
            return {"id": item["id"], "responses": [], "error": None}

        async def run_multi(self, client, item):
            raise AssertionError((client, item))

    raw_path = tmp_path / "raw_responses.jsonl"
    raw_by_id = {}
    stage = asyncio.create_task(
        run_chat_stage(
            runner=FakeRunner(),
            client=object(),
            singles=[{"id": "fast"}, {"id": "slow"}],
            multis=[],
            raw_path=raw_path,
            raw_by_id=raw_by_id,
        )
    )

    for _ in range(20):
        if raw_path.exists() and raw_path.read_text(encoding="utf-8").strip():
            break
        await asyncio.sleep(0)

    assert [row["id"] for row in load_jsonl(raw_path)] == ["fast"]
    assert not stage.done()
    release.set()
    await stage


@pytest.mark.asyncio
async def test_judge_result_retries_transient_failure(monkeypatch):
    import evaluation.run_evaluation as runner_module

    attempts = 0

    async def fake_generate_answer(prompt: str, purpose: str):
        del prompt, purpose
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("temporary provider failure")
        return {"answer": '{"id":"item_1","score":88,"critical_error":false}'}

    monkeypatch.setattr(runner_module, "generate_answer", fake_generate_answer)
    item = {"id": "item_1"}
    result = {
        "id": "item_1",
        "subset": "single_turn",
        "source_case": "case01",
        "primary_capability": "Q1",
        "responses": [{"answer": "reply"}],
        "error": None,
    }

    judged = await judge_result(
        item, result, "protocol", asyncio.Semaphore(1), attempts=2
    )

    assert judged["score"] == 88.0
    assert attempts == 2


@pytest.mark.asyncio
async def test_lifespan_skips_eyun_worker_in_evaluation_mode(monkeypatch):
    import app.main as main_module

    async def fail_worker(stop_event):
        del stop_event
        raise AssertionError("evaluation mode must not start the Eyun worker")

    monkeypatch.setattr(
        main_module,
        "get_settings",
        lambda: SimpleNamespace(evaluation_mode=True),
    )
    monkeypatch.setattr(main_module, "eyun_risk_control_worker", fail_worker)
    app = SimpleNamespace(state=SimpleNamespace())

    async with main_module.lifespan(app):
        assert not hasattr(app.state, "eyun_risk_control_task")


def test_evaluation_request_is_detected_from_runner_metadata():
    from app.services.chat_orchestrator import _is_evaluation_request

    assert _is_evaluation_request(SimpleNamespace(metadata={"evaluation_id": "c01_n02"}))
    assert not _is_evaluation_request(SimpleNamespace(metadata={}))

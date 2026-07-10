import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from evaluation.run_evaluation import (
    aggregate_scores,
    append_jsonl,
    build_evaluation_metadata,
    calculate_judge_score,
    build_single_message,
    choose_pending_items,
    judge_result,
    EvaluationRunner,
    is_successful_chat_result,
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


def test_evaluation_metadata_uses_structured_business_snapshot():
    item = {
        "conversation": [{"role": "user", "content": "给我推荐一款。"}],
        "business_snapshot": "《东方红荷》2—3苗26.8元。",
        "tool_state": {"stock": 3},
    }

    metadata = build_evaluation_metadata(item)

    assert metadata == {
        "business_snapshot": "《东方红荷》2—3苗26.8元。",
        "tool_state": {"stock": 3},
    }


def test_build_single_message_uses_structured_customer_context():
    item = {
        "conversation": [{"role": "user", "content": "现在该怎么办？"}],
        "customer_context": "客户在西安，刚入门，正在排查黑根和空根。",
    }

    message = build_single_message(item)

    assert "客户在西安，刚入门" in message
    assert message.endswith("现在该怎么办？")


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


def test_calculate_judge_score_uses_rubric_statuses_instead_of_model_total():
    item = {
        "task_type": "single_turn",
        "scoring": {
            "must_have_points": 70,
            "should_have_points": 20,
            "expression_points": 10,
            "critical_error_cap": 40,
        },
    }
    judged = {
        "score": 99,
        "must_have": [{"status": "met"}, {"status": "partial"}],
        "should_have": [{"status": "met"}, {"status": "missed"}],
        "expression_score": 8,
        "violations": [
            {"triggered": True, "critical": False},
            {"triggered": False, "critical": True},
        ],
    }

    assert calculate_judge_score(item, judged) == (60.5, False)


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
        "done": {
            "id": "done",
            "responses": [{"session_id": "s1"}],
            "error": None,
        },
        "failed": {"id": "failed", "error": "timeout"},
    }

    assert choose_pending_items(items, existing) == [
        {"id": "failed"},
        {"id": "new"},
    ]


def test_empty_response_row_is_not_completed():
    row = {"id": "empty", "responses": [], "error": ""}

    assert is_successful_chat_result(row) is False
    assert choose_pending_items([{"id": "empty"}], {"empty": row}) == [
        {"id": "empty"}
    ]


@pytest.mark.asyncio
async def test_boundary_item_runs_as_chat_and_passes_tool_state(monkeypatch):
    runner = EvaluationRunner("http://example.test", "key", "kb", 1)
    captured = {}

    async def fake_chat(client, **kwargs):
        del client
        captured.update(kwargs)
        return {"answer": "reply", "session_id": "s1", "latency_ms": 1}

    monkeypatch.setattr(runner, "_chat", fake_chat)
    item = {
        "id": "b01",
        "task_type": "boundary",
        "derived_from": "case01",
        "primary_capability": "G5",
        "conversation": [{"role": "user", "content": "stop"}],
        "tool_state": {"activity": "expired"},
    }

    result = await runner.run_single(object(), item)

    assert result["subset"] == "boundary"
    assert result["source_case"] == "case01"
    assert captured["metadata"]["tool_state"] == {"activity": "expired"}


@pytest.mark.asyncio
async def test_multi_turn_passes_history_as_metadata_not_current_message(monkeypatch):
    runner = EvaluationRunner("http://example.test", "key", "kb", 1)
    messages = []

    async def fake_chat(client, **kwargs):
        del client
        messages.append((kwargs["message"], kwargs["metadata"]))
        turn = len(messages)
        return {
            "answer": f"assistant {turn}",
            "session_id": "s1",
            "latency_ms": 1,
        }

    monkeypatch.setattr(runner, "_chat", fake_chat)
    item = {
        "id": "m01",
        "source_case": "case01",
        "customer_turns": ["customer one", "customer two"],
        "initial_context": "known background",
        "turn_metadata": [
            {},
            {
                "business_snapshot": "会员39.9元。",
                "tool_state": {"order_status": "unverified"},
            },
        ],
    }

    await runner.run_multi(object(), item)

    second_message, second_metadata = messages[1]
    assert second_message == "customer two"
    assert second_metadata["business_snapshot"] == "会员39.9元。"
    assert second_metadata["tool_state"] == {"order_status": "unverified"}
    assert second_metadata["evaluation_context"] == {
        "customer_context": "known background",
        "recent_turns": [
            {"role": "user", "content": "customer one"},
            {"role": "assistant", "content": "assistant 1"},
        ],
    }


@pytest.mark.asyncio
async def test_expected_handoff_is_scored_from_action_without_calling_judge(monkeypatch):
    import evaluation.run_evaluation as runner_module

    async def fail_generate_answer(prompt: str, purpose: str):
        raise AssertionError((prompt, purpose))

    monkeypatch.setattr(runner_module, "generate_answer", fail_generate_answer)
    item = {"id": "b15", "expected_action": "human_handoff"}
    result = {
        "id": "b15",
        "subset": "boundary",
        "source_case": "boundary",
        "primary_capability": "O8",
        "responses": [
            {
                "answer": "",
                "route": "human",
                "need_human": True,
                "next_action": "human_handoff",
            }
        ],
        "error": None,
    }

    judged = await judge_result(
        item, result, "protocol", asyncio.Semaphore(1), attempts=1
    )

    assert judged["score"] == 100
    assert judged["action_match"] is True
    assert judged["judge_error"] is None


@pytest.mark.asyncio
async def test_chat_latency_excludes_semaphore_wait():
    runner = EvaluationRunner("http://example.test", "key", "kb", 1)

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "code": 0,
                "data": {"answer": "ok", "session_id": "s1"},
            }

    class Client:
        async def post(self, *args, **kwargs):
            del args, kwargs
            return Response()

    await runner.semaphore.acquire()
    task = asyncio.create_task(
        runner._chat(
            Client(), item_id="i1", user_id="u1", message="hello"
        )
    )
    await asyncio.sleep(0.05)
    runner.semaphore.release()
    result = await task

    assert result["queue_wait_ms"] >= 40
    assert result["latency_ms"] < 40


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
    from app.services.chat_orchestrator import _apply_evaluation_context, _is_evaluation_request

    assert _is_evaluation_request(SimpleNamespace(metadata={"evaluation_id": "c01_n02"}))
    assert not _is_evaluation_request(SimpleNamespace(metadata={}))

    state = SimpleNamespace(metadata={})
    _apply_evaluation_context(
        SimpleNamespace(
            metadata={
                "evaluation_context": {
                    "customer_context": "客户在西安，刚入门。",
                    "recent_turns": [{"role": "user", "content": "之前烂根。"}],
                }
            }
        ),
        state,
    )
    assert state.metadata["profile"]["ai_summary"] == "客户在西安，刚入门。"
    assert state.metadata["recent_turns"] == [
        {"role": "user", "content": "之前烂根。"}
    ]

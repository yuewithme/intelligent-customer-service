import json

from evaluation.run_evaluation import (
    aggregate_scores,
    build_single_message,
    parse_judge_json,
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

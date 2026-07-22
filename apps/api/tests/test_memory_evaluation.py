import json
import re
from pathlib import Path

import pytest

from evaluation.memory.run_memory_eval import (
    load_jsonl,
    perfect_predictions,
    recent_only_predictions,
    score_predictions,
    validate_case,
    validate_dataset,
)


DATASET = (
    Path(__file__).resolve().parents[3]
    / "datasets"
    / "evaluation"
    / "memory"
    / "memory_v2_seed.jsonl"
)
RECENT_ONLY_BASELINE = DATASET.parent / "baselines" / "recent_only.json"


def test_memory_seed_dataset_is_valid_and_covers_contract_categories():
    cases = load_jsonl(DATASET)

    validate_dataset(cases)

    assert len(cases) >= 18
    assert {case["category"] for case in cases} == {
        "abstention",
        "deletion",
        "extraction",
        "retrieval",
        "security",
        "temporal_update",
    }


def test_memory_seed_dataset_has_no_obvious_direct_identifiers():
    content = DATASET.read_text(encoding="utf-8")

    assert not re.search(r"\b1[3-9]\d{9}\b", content)
    assert not re.search(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", content, re.I)
    assert not re.search(r"(?:access|api|secret)[_-]?token\s*[:=]", content, re.I)


def test_memory_contract_rejects_dangling_evidence():
    case = load_jsonl(DATASET)[0]
    case["expected"]["operations"][0]["evidence_event_ids"] = ["missing_event"]

    errors = validate_case(case)

    assert any("dangling evidence" in error for error in errors)


def test_memory_contract_rejects_assistant_only_customer_fact():
    case = {
        "schema_version": "memory_eval.v1",
        "id": "invalid-assistant-fact",
        "category": "extraction",
        "tenant_id": "tenant_alpha",
        "subject_id": "subject_invalid",
        "events": [
            {
                "event_id": "evt_invalid",
                "event_type": "assistant_message",
                "actor_type": "assistant",
                "content": "你喜欢树皮。",
                "occurred_at": "2026-01-01T00:00:00Z",
            }
        ],
        "expected": {
            "operations": [
                {
                    "operation": "ADD",
                    "memory_kind": "semantic_fact",
                    "fact_key": "service.preference",
                    "fact_value": {"topic": "medium", "value": "bark"},
                    "evidence_event_ids": ["evt_invalid"],
                }
            ]
        },
    }

    assert any("assistant-only" in error for error in validate_case(case))


def test_oracle_scores_all_memory_contract_metrics_perfectly():
    cases = load_jsonl(DATASET)

    report = score_predictions(cases, perfect_predictions(cases))

    assert report["operation_f1"] == 1.0
    assert report["evidence_grounding_rate"] == 1.0
    assert report["retrieval_recall_at_5"] == 1.0
    assert report["temporal_operation_accuracy"] == 1.0
    assert report["cross_tenant_retrievals"] == 0
    assert report["deletion_residue_count"] == 0


def test_recent_only_baseline_exposes_legacy_memory_limits():
    cases = load_jsonl(DATASET)

    report = score_predictions(cases, recent_only_predictions(cases))

    assert report["operation_recall"] == 0.0
    assert report["retrieval_recall_at_5"] < 1.0
    assert report["deletion_residue_count"] > 0


def test_committed_recent_only_baseline_matches_current_contract():
    cases = load_jsonl(DATASET)
    expected = json.loads(RECENT_ONLY_BASELINE.read_text(encoding="utf-8"))

    actual = {
        "mode": "recent_only",
        **score_predictions(cases, recent_only_predictions(cases)),
    }

    assert actual == expected


def test_dataset_validator_reports_duplicate_case_ids():
    case = load_jsonl(DATASET)[0]

    with pytest.raises(ValueError, match="duplicate case IDs"):
        validate_dataset([case, case])

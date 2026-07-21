import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "memory_eval.v1"
ALLOWED_CATEGORIES = {
    "abstention",
    "deletion",
    "extraction",
    "retrieval",
    "security",
    "temporal_update",
}
ALLOWED_OPERATIONS = {
    "ADD",
    "REINFORCE",
    "SUPERSEDE",
    "DISPUTE",
    "RESOLVE",
    "NOOP",
}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_number}: invalid JSON: {exc}") from exc
    return rows


def _event_map(case: dict[str, Any]) -> dict[str, dict[str, Any]]:
    events = [*(case.get("events") or []), *(case.get("distractor_events") or [])]
    return {event["event_id"]: event for event in events}


def validate_case(case: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    case_id = str(case.get("id") or "<missing-id>")
    for field in ("schema_version", "id", "category", "tenant_id", "subject_id"):
        if not case.get(field):
            errors.append(f"{case_id}: missing {field}")
    if case.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"{case_id}: unsupported schema_version")
    if case.get("category") not in ALLOWED_CATEGORIES:
        errors.append(f"{case_id}: unsupported category")

    events = [*(case.get("events") or []), *(case.get("distractor_events") or [])]
    event_ids = [event.get("event_id") for event in events]
    if not case.get("events"):
        errors.append(f"{case_id}: events must not be empty")
    if any(not event_id for event_id in event_ids):
        errors.append(f"{case_id}: every event requires event_id")
    if len(event_ids) != len(set(event_ids)):
        errors.append(f"{case_id}: duplicate event_id")

    expected = case.get("expected")
    if not isinstance(expected, dict):
        return [*errors, f"{case_id}: missing expected object"]
    event_by_id = _event_map(case)
    primary_ids = {event["event_id"] for event in case.get("events") or []}
    for operation in expected.get("operations") or []:
        op = operation.get("operation")
        if op not in ALLOWED_OPERATIONS:
            errors.append(f"{case_id}: unsupported operation {op}")
        evidence = operation.get("evidence_event_ids") or []
        if op != "NOOP" and not evidence:
            errors.append(f"{case_id}: {op} requires evidence")
        dangling = set(evidence) - primary_ids
        if dangling:
            errors.append(f"{case_id}: dangling evidence {sorted(dangling)}")
        if operation.get("memory_kind") == "semantic_fact" and evidence:
            actors = {event_by_id[event_id].get("actor_type") for event_id in evidence if event_id in event_by_id}
            if actors == {"assistant"}:
                errors.append(f"{case_id}: assistant-only customer fact evidence")

    retrieval_ids = set(expected.get("retrieved_event_ids") or [])
    dangling_retrieval = retrieval_ids - primary_ids
    if dangling_retrieval:
        errors.append(f"{case_id}: dangling retrieval IDs {sorted(dangling_retrieval)}")
    return errors


def validate_dataset(cases: list[dict[str, Any]]) -> None:
    errors: list[str] = []
    ids = [case.get("id") for case in cases]
    if len(ids) != len(set(ids)):
        errors.append("dataset contains duplicate case IDs")
    for case in cases:
        errors.extend(validate_case(case))
    if errors:
        raise ValueError("\n".join(errors))


def _canonical_operation(operation: dict[str, Any]) -> str:
    comparable = {
        "operation": operation.get("operation"),
        "memory_kind": operation.get("memory_kind"),
        "fact_key": operation.get("fact_key"),
        "fact_value": operation.get("fact_value"),
        "evidence_event_ids": sorted(operation.get("evidence_event_ids") or []),
    }
    return json.dumps(comparable, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def recent_only_predictions(cases: list[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
    predictions = []
    for case in cases:
        ordered = sorted(case["events"], key=lambda event: event["occurred_at"])
        predictions.append(
            {
                "id": case["id"],
                "operations": [],
                "retrieved_event_ids": [event["event_id"] for event in ordered[-limit:]],
                "must_abstain": False,
                "remaining_artifact_ids": case.get("expected", {}).get("deleted_artifact_ids", []),
            }
        )
    return predictions


def perfect_predictions(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "id": case["id"],
            "operations": case["expected"].get("operations", []),
            "retrieved_event_ids": case["expected"].get("retrieved_event_ids", []),
            "must_abstain": bool(case["expected"].get("must_abstain", False)),
            "remaining_artifact_ids": [],
        }
        for case in cases
    ]


def score_predictions(
    cases: list[dict[str, Any]], predictions: list[dict[str, Any]]
) -> dict[str, Any]:
    prediction_by_id = {row.get("id"): row for row in predictions}
    expected_ops = predicted_ops = matched_ops = 0
    grounded_ops = operations_with_required_evidence = 0
    retrieval_hits = retrieval_expected = retrieval_cases = 0
    abstention_correct = temporal_correct = temporal_total = 0
    cross_tenant_leaks = forbidden_fact_violations = deletion_residue = 0
    category_counts: Counter[str] = Counter()

    for case in cases:
        category_counts[case["category"]] += 1
        expected = case["expected"]
        predicted = prediction_by_id.get(case["id"], {})
        expected_set = {_canonical_operation(op) for op in expected.get("operations") or []}
        predicted_set = {_canonical_operation(op) for op in predicted.get("operations") or []}
        expected_ops += len(expected_set)
        predicted_ops += len(predicted_set)
        matched_ops += len(expected_set & predicted_set)

        allowed_events = _event_map(case)
        primary_ids = {event["event_id"] for event in case["events"]}
        for operation in predicted.get("operations") or []:
            if operation.get("operation") == "NOOP":
                continue
            operations_with_required_evidence += 1
            evidence = operation.get("evidence_event_ids") or []
            if evidence and all(event_id in primary_ids for event_id in evidence):
                grounded_ops += 1
            if operation.get("fact_key") in set(expected.get("forbidden_fact_keys") or []):
                forbidden_fact_violations += 1

        expected_retrieval = set(expected.get("retrieved_event_ids") or [])
        if expected_retrieval:
            retrieval_cases += 1
            predicted_top5 = (predicted.get("retrieved_event_ids") or [])[:5]
            retrieval_hits += len(expected_retrieval & set(predicted_top5))
            retrieval_expected += len(expected_retrieval)
        for event_id in predicted.get("retrieved_event_ids") or []:
            event = allowed_events.get(event_id)
            if event and event.get("tenant_id", case["tenant_id"]) != case["tenant_id"]:
                cross_tenant_leaks += 1

        abstention_correct += bool(predicted.get("must_abstain", False)) == bool(
            expected.get("must_abstain", False)
        )
        if case["category"] == "temporal_update":
            temporal_total += 1
            temporal_correct += expected_set == predicted_set
        if case["category"] == "deletion":
            deletion_residue += len(predicted.get("remaining_artifact_ids") or [])

    precision = matched_ops / predicted_ops if predicted_ops else (1.0 if not expected_ops else 0.0)
    recall = matched_ops / expected_ops if expected_ops else 1.0
    operation_f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    case_count = len(cases)
    return {
        "schema_version": SCHEMA_VERSION,
        "case_count": case_count,
        "category_counts": dict(sorted(category_counts.items())),
        "operation_precision": round(precision, 4),
        "operation_recall": round(recall, 4),
        "operation_f1": round(operation_f1, 4),
        "evidence_grounding_rate": round(
            grounded_ops / operations_with_required_evidence, 4
        ) if operations_with_required_evidence else 1.0,
        "retrieval_recall_at_5": round(retrieval_hits / retrieval_expected, 4)
        if retrieval_expected else 1.0,
        "retrieval_case_count": retrieval_cases,
        "abstention_accuracy": round(abstention_correct / case_count, 4)
        if case_count else 1.0,
        "temporal_operation_accuracy": round(temporal_correct / temporal_total, 4)
        if temporal_total else 1.0,
        "forbidden_fact_violations": forbidden_fact_violations,
        "cross_tenant_retrievals": cross_tenant_leaks,
        "deletion_residue_count": deletion_residue,
    }


def parse_args() -> argparse.Namespace:
    default_dataset = Path(__file__).parent / "datasets" / "memory_v2_seed.jsonl"
    parser = argparse.ArgumentParser(description="Validate and score Memory 2.0 cases")
    parser.add_argument("--dataset", type=Path, default=default_dataset)
    parser.add_argument("--predictions", type=Path)
    parser.add_argument(
        "--baseline", choices=("recent_only", "oracle"), default="recent_only"
    )
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    cases = load_jsonl(args.dataset)
    validate_dataset(cases)
    if args.predictions:
        predictions = load_jsonl(args.predictions)
        mode = "predictions"
    elif args.baseline == "oracle":
        predictions = perfect_predictions(cases)
        mode = "oracle"
    else:
        predictions = recent_only_predictions(cases)
        mode = "recent_only"
    report = {"mode": mode, **score_predictions(cases, predictions)}
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

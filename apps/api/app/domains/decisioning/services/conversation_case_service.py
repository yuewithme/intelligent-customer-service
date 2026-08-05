from __future__ import annotations

import json
from pathlib import Path
from typing import Any


_CASE_ROOT = Path(__file__).resolve().parents[1] / "data" / "conversation_cases"
_CASE_DIRS = {
    "complete": _CASE_ROOT / "complete",
    "cleaned": _CASE_ROOT / "cleaned",
}


def load_conversation_cases(
    library_type: str = "complete",
) -> list[dict[str, Any]]:
    case_dir = _case_directory(library_type)
    cases = []
    for path in sorted(case_dir.glob("*.json"), key=_case_sort_key):
        payload = json.loads(path.read_text(encoding="utf-8"))
        cases.append(_normalize_case(payload, path.name, library_type))
    return cases


def list_conversation_cases(
    *, keyword: str | None = None, library_type: str = "complete"
) -> dict[str, Any]:
    cases = load_conversation_cases(library_type)
    if keyword:
        normalized = keyword.strip().lower()
        cases = [
            case
            for case in cases
            if normalized in case["case_id"].lower()
            or normalized in case["preview"].lower()
        ]
    return {
        "items": [_case_summary(case) for case in cases],
        "total": len(cases),
        "library_counts": {
            name: len(load_conversation_cases(name)) for name in _CASE_DIRS
        },
    }


def get_conversation_case(
    case_id: str, library_type: str = "complete"
) -> dict[str, Any] | None:
    return next(
        (
            case
            for case in load_conversation_cases(library_type)
            if case["case_id"] == case_id
        ),
        None,
    )


def export_conversation_cases(
    library_type: str = "complete",
) -> list[dict[str, Any]]:
    return load_conversation_cases(library_type)


def _normalize_case(
    payload: dict[str, Any], file_name: str, library_type: str
) -> dict[str, Any]:
    case_id = str(payload["case_id"])
    turns = []
    for index, raw_turn in enumerate(payload.get("turns") or [], start=1):
        role = str(raw_turn.get("role") or "")
        if role not in {"customer", "merchant"}:
            raise ValueError(f"{file_name}: unsupported role {role!r}")
        messages = [
            str(message).strip()
            for message in raw_turn.get("messages") or []
            if str(message).strip()
        ]
        if messages:
            turns.append(
                {
                    "turn_id": f"{case_id}:turn:{index:03d}",
                    "role": role,
                    "messages": messages,
                    "reference_only": role == "merchant",
                }
            )
    checkpoints = _build_checkpoints(case_id, turns)
    preview = next(
        (
            message
            for turn in turns
            if turn["role"] == "customer"
            for message in turn["messages"]
        ),
        "",
    )
    return {
        "schema_version": "conversation_case.v1",
        "case_id": case_id,
        "legacy_case_id": payload.get("legacy_case_id"),
        "library_type": library_type,
        "usage": str(payload.get("usage") or ""),
        "source_file": str(payload.get("source_file") or file_name),
        "content_quality": str(
            payload.get("content_quality")
            or (
                "reconstructed_from_summary"
                if case_id == "case10"
                else "cleaned_transcript"
            )
        ),
        "preview": preview,
        "turn_count": len(turns),
        "message_count": sum(len(turn["messages"]) for turn in turns),
        "customer_turn_count": sum(turn["role"] == "customer" for turn in turns),
        "reference_turn_count": sum(turn["role"] == "merchant" for turn in turns),
        "checkpoint_count": len(checkpoints),
        "reference_policy": (
            "Historical merchant replies are comparison references, not gold answers."
        ),
        "turns": turns,
        "checkpoints": checkpoints,
    }


def _build_checkpoints(
    case_id: str, turns: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    checkpoints = []
    for index, turn in enumerate(turns):
        if turn["role"] != "customer":
            continue
        references = []
        for following in turns[index + 1 :]:
            if following["role"] == "customer":
                break
            references.extend(following["messages"])
        checkpoints.append(
            {
                "checkpoint_id": f"{case_id}:checkpoint:{len(checkpoints) + 1:03d}",
                "turn_id": turn["turn_id"],
                "customer_message": "\n".join(turn["messages"]),
                "reference_reply": "\n".join(references),
            }
        )
    return checkpoints


def _case_summary(case: dict[str, Any]) -> dict[str, Any]:
    return {
        key: case[key]
        for key in (
            "case_id",
            "legacy_case_id",
            "library_type",
            "usage",
            "source_file",
            "content_quality",
            "preview",
            "turn_count",
            "message_count",
            "customer_turn_count",
            "reference_turn_count",
            "checkpoint_count",
        )
    }


def _case_directory(library_type: str) -> Path:
    try:
        return _CASE_DIRS[library_type]
    except KeyError as exc:
        raise ValueError(
            f"unsupported conversation case library: {library_type}"
        ) from exc


def _case_sort_key(path: Path) -> int:
    return int(path.stem.removeprefix("case"))

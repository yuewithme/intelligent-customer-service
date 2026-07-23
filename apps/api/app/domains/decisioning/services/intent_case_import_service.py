from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.domains.conversations.schemas.event import NormalizedMessage
from app.domains.decisioning.schemas.intent import IntentResult
from app.domains.decisioning.services.intent_observation_service import (
    get_intent_observation,
    record_intent_observation,
)


CASE_DATA_DIR = (
    Path(__file__).resolve().parents[1] / "data" / "intent_labeling_cases"
)
ROLE_MAP = {"customer": "user", "merchant": "assistant"}


def list_intent_labeling_cases() -> list[str]:
    return sorted(path.stem for path in CASE_DATA_DIR.glob("case*.json"))


def load_intent_labeling_case(case_id: str) -> dict[str, Any]:
    normalized_case_id = str(case_id or "").strip().lower()
    if not normalized_case_id or not normalized_case_id.replace("_", "").isalnum():
        raise ValueError("case_id is invalid")
    path = CASE_DATA_DIR / f"{normalized_case_id}.json"
    if not path.is_file():
        raise FileNotFoundError(f"intent labeling case not found: {normalized_case_id}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    _validate_case_payload(payload, expected_case_id=normalized_case_id)
    return payload


def normalize_case_turns(turns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for raw_turn in turns:
        if not isinstance(raw_turn, dict):
            raise ValueError("each case turn must be an object")
        source_role = str(raw_turn.get("role") or "").strip()
        role = ROLE_MAP.get(source_role)
        if role is None:
            raise ValueError(f"unsupported case turn role: {source_role}")
        raw_messages = raw_turn.get("messages")
        if not isinstance(raw_messages, list):
            raise ValueError("case turn messages must be a list")
        messages = [
            str(message).strip()
            for message in raw_messages
            if str(message or "").strip()
        ]
        if not messages:
            continue
        if normalized and normalized[-1]["role"] == role:
            normalized[-1]["messages"].extend(messages)
            normalized[-1]["content"] = "\n".join(normalized[-1]["messages"])
            continue
        normalized.append(
            {
                "role": role,
                "messages": messages,
                "content": "\n".join(messages),
            }
        )
    return normalized


async def import_intent_labeling_case(case_id: str) -> dict[str, Any]:
    payload = load_intent_labeling_case(case_id)
    turns = normalize_case_turns(payload["turns"])
    case_id = payload["case_id"]
    customer_id = payload["customer_id"]
    imported_trace_ids: list[str] = []
    customer_turn_number = 0

    for turn_index, turn in enumerate(turns):
        if turn["role"] != "user":
            continue
        customer_turn_number += 1
        trace_id = f"intent-case-{case_id}-{customer_turn_number:03d}"
        message_id = f"{case_id}-customer-{customer_turn_number:03d}"
        context = [
            {"role": previous["role"], "content": previous["content"]}
            for previous in turns[:turn_index]
        ]
        intent = IntentResult(
            route="clarify",
            primary_intent="unknown",
            primary_domain=None,
            primary_goal=None,
            scope="ambiguous",
            classifier_source="case_import",
            raw_prediction={
                "source_case": case_id,
                "source_file": payload.get("source_file"),
                "content_quality": payload.get(
                    "content_quality", "verbatim_case_transcript"
                ),
                "customer_turn": customer_turn_number,
                "source_message_count": len(turn["messages"]),
            },
            confidence=0.0,
            reason="案例导入，等待人工标注",
        )
        message = NormalizedMessage(
            trace_id=trace_id,
            channel="case",
            user_id=customer_id,
            session_id=case_id,
            message_id=message_id,
            message=turn["content"],
            kb_id=get_settings().wechat_default_kb_id,
            metadata={
                "origin": "case_import",
                "skip_conversation_locator": True,
                "source_case": case_id,
            },
        )
        await record_intent_observation(
            message=message,
            intent=intent,
            candidates=[],
            context=context,
        )
        if await get_intent_observation(trace_id) is None:
            raise RuntimeError(f"intent observation was not persisted: {trace_id}")
        imported_trace_ids.append(trace_id)

    return {
        "case_id": case_id,
        "customer_id": customer_id,
        "customer_name": payload.get("customer_name"),
        "observation_count": len(imported_trace_ids),
        "trace_ids": imported_trace_ids,
    }


def _validate_case_payload(
    payload: Any,
    *,
    expected_case_id: str,
) -> None:
    if not isinstance(payload, dict):
        raise ValueError("case payload must be an object")
    if payload.get("case_id") != expected_case_id:
        raise ValueError("case_id does not match the data file name")
    if not str(payload.get("customer_id") or "").strip():
        raise ValueError("customer_id is required")
    if not isinstance(payload.get("turns"), list) or not payload["turns"]:
        raise ValueError("case turns are required")

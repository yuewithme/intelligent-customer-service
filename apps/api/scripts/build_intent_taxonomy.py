from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


CARD_HEADING = re.compile(r"^##\s+([DGI])\d+\s+(.+?)｜(.+?)\s*$")
FIELD_HEADING = re.compile(r"^-\s+\*\*(.+?)\*\*[:：]\s*(.*)$")
FIELD_NAMES = {
    "定义": "definition",
    "应当命中": "include",
    "不应命中": "exclude",
    "初始正例": "positive_examples",
    "初始反例": "negative_examples",
    "易混淆标签": "confusable",
    "建议槽位": "slots",
    "销售信号/阶段影响": "stage_effect",
}
KIND_NAMES = {"D": "domain", "G": "goal", "I": "issue"}


def _clean(value: str) -> str:
    value = value.strip().replace("\\_", "_").replace("\\-", "-")
    value = value.replace("【", "").replace("】", "")
    return value.strip()


def _parse_list(lines: list[str]) -> list[str]:
    values: list[str] = []
    for line in lines:
        item = re.sub(r"^\s*\d+[\.、]\s*", "", line).strip()
        if not item or item.startswith("<!--") or item in {"---"}:
            continue
        item = _clean(item)
        if re.match(r"^(?:待填写|待补充|待评审|待确认)", item):
            continue
        if item:
            values.append(item)
    return values


def parse_taxonomy(source: Path) -> dict:
    lines = source.read_text(encoding="utf-8-sig").splitlines()
    cards: list[dict] = []
    index = 0
    while index < len(lines):
        heading = CARD_HEADING.match(lines[index])
        if not heading:
            index += 1
            continue
        prefix, raw_id, raw_name = heading.groups()
        end = index + 1
        while end < len(lines) and not CARD_HEADING.match(lines[end]):
            end += 1
        block = lines[index + 1 : end]
        fields: dict[str, list[str]] = {}
        current: str | None = None
        for line in block:
            field = FIELD_HEADING.match(line.strip())
            if field:
                raw_field, inline = field.groups()
                current = FIELD_NAMES.get(raw_field)
                if current:
                    fields[current] = [inline] if inline.strip() else []
                continue
            if current and line.strip():
                fields[current].append(line)

        card = {
            "kind": KIND_NAMES[prefix],
            "id": _clean(raw_id),
            "name": _clean(raw_name),
            "definition": " ".join(_parse_list(fields.get("definition", []))),
            "include": " ".join(_parse_list(fields.get("include", []))),
            "exclude": " ".join(_parse_list(fields.get("exclude", []))),
            "positive_examples": _parse_list(fields.get("positive_examples", [])),
            "negative_examples": _parse_list(fields.get("negative_examples", [])),
            "confusable": [
                _clean(value)
                for value in ",".join(_parse_list(fields.get("confusable", []))).split(",")
                if _clean(value)
            ],
            "slots": [
                _clean(value)
                for value in ",".join(_parse_list(fields.get("slots", []))).split(",")
                if _clean(value)
            ],
            "stage_effect": " ".join(_parse_list(fields.get("stage_effect", []))),
        }
        cards.append(card)
        index = end

    counts = {
        kind: sum(card["kind"] == kind for card in cards)
        for kind in ("domain", "goal", "issue")
    }
    if counts != {"domain": 8, "goal": 25, "issue": 28}:
        raise ValueError(f"Unexpected taxonomy counts: {counts}")
    return {"version": "1.0", "counts": counts, "labels": cards}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    payload = parse_taxonomy(args.source)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()

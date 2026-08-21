from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

from app.domains.catalog.services.agent_media_copy_service import (
    copy_ref_for,
    media_copy_for,
)


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".webp"}


def build_catalog(root: Path, *, version: int) -> dict[str, Any]:
    manifest_path = root / "manifest.json"
    rows = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    if not isinstance(rows, list):
        raise ValueError("manifest.json must contain a list")

    catalog: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("manifest rows must be objects")
        category = str(row.get("category") or "").strip()
        relative_path = str(row.get("relative_path") or "").replace("\\", "/")
        topic_title = _topic_title(relative_path)
        copy = media_copy_for(
            title=topic_title,
            category=category,
            variant_key=relative_path,
        )
        copy_ref = copy_ref_for(
            category=category,
            topic=copy["copy_topic"],
            variant_key=relative_path,
        )
        row.update(
            {
                "copy_ref": copy_ref,
                "copy_topic": copy["copy_topic"],
                "copy_type": copy["copy_type"],
                "copy_text": copy["copy_text"],
                "copy_source": copy["copy_source"],
                "copy_version": version,
                "copy_status": "ready",
            }
        )
        item = catalog.setdefault(
            copy_ref,
            {
                "copy_ref": copy_ref,
                "topic": copy["copy_topic"],
                "category": category,
                "copy_type": copy["copy_type"],
                "copy_text": copy["copy_text"],
                "copy_source": copy["copy_source"],
                "copy_version": version,
                "status": "ready",
                "material_count": 0,
            },
        )
        item["material_count"] += 1

    catalog_rows = sorted(
        catalog.values(), key=lambda item: (item["copy_type"], item["topic"])
    )
    _write_json_atomic(manifest_path, rows)
    _write_json_atomic(root / "copy_catalog.json", catalog_rows)
    _write_csv_atomic(root / "copy_catalog.csv", catalog_rows)
    return {
        "materials": len(rows),
        "topics": len(catalog_rows),
        "copy_types": dict(Counter(row["copy_type"] for row in rows)),
        "copy_sources": dict(Counter(row["copy_source"] for row in rows)),
        "missing": sum(not str(row.get("copy_text") or "").strip() for row in rows),
        "version": version,
    }


def _topic_title(relative_path: str) -> str:
    path = Path(relative_path)
    if path.suffix.lower() in IMAGE_SUFFIXES and path.parent.name:
        return path.parent.name
    return path.stem


def _write_json_atomic(path: Path, value: Any) -> None:
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _write_csv_atomic(path: Path, rows: list[dict[str, Any]]) -> None:
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    fieldnames = [
        "copy_ref",
        "topic",
        "category",
        "copy_type",
        "copy_text",
        "copy_source",
        "copy_version",
        "status",
        "material_count",
    ]
    with temporary.open("w", encoding="utf-8-sig", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--version", type=int, default=1)
    args = parser.parse_args()
    result = build_catalog(args.root.resolve(), version=max(1, args.version))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()

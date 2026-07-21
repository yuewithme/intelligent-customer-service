import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from app.db.models import YouzanProductKnowledgeModel, YouzanProductModel
from app.db.session import get_session_factory
from app.services.product_sales_copy_service import generate_product_sales_copy


def _eligible_records() -> list[dict]:
    with get_session_factory()() as session:
        rows = session.execute(
            select(YouzanProductModel, YouzanProductKnowledgeModel)
            .join(
                YouzanProductKnowledgeModel,
                YouzanProductKnowledgeModel.item_id == YouzanProductModel.item_id,
            )
            .where(YouzanProductModel.status == "on_sale")
            .order_by(YouzanProductKnowledgeModel.id.asc())
        ).all()
        return [
            {
                "id": knowledge.id,
                "item_id": product.item_id,
                "product_name": knowledge.product_name,
                "category": knowledge.category,
                "flower_color": knowledge.flower_color,
                "fragrance": knowledge.fragrance,
                "bloom_period": knowledge.bloom_period,
                "care_scenes": knowledge.care_scenes,
                "audience_tag": knowledge.audience_tag,
                "highlighted_features": knowledge.highlighted_features,
                "sales_copy": knowledge.sales_copy,
            }
            for product, knowledge in rows
        ]


def _write_json(path: str, records: list[dict]) -> None:
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(records),
        "records": records,
    }
    Path(path).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


async def _generate(path: str, concurrency: int) -> None:
    records = _eligible_records()
    semaphore = asyncio.Semaphore(concurrency)

    async def generate(record: dict) -> dict:
        async with semaphore:
            copy = await generate_product_sales_copy(record)
        return {
            "id": record["id"],
            "item_id": record["item_id"],
            "product_name": record["product_name"],
            "sales_copy": copy,
        }

    generated = await asyncio.gather(*(generate(record) for record in records))
    _write_json(path, generated)
    print(json.dumps({"generated": len(generated), "output": path}, ensure_ascii=False))


def _export(path: str) -> None:
    records = [
        {
            "id": record["id"],
            "item_id": record["item_id"],
            "product_name": record["product_name"],
            "sales_copy": record["sales_copy"],
        }
        for record in _eligible_records()
    ]
    _write_json(path, records)
    print(json.dumps({"exported": len(records), "output": path}, ensure_ascii=False))


def _apply(path: str) -> None:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    records = payload.get("records") if isinstance(payload, dict) else None
    if not isinstance(records, list) or not records:
        raise ValueError("话术文件没有可写入的数据")
    current = _eligible_records()
    expected = {(item["id"], item["item_id"]) for item in current}
    incoming = {(int(item["id"]), str(item["item_id"])) for item in records}
    if incoming != expected:
        raise ValueError("话术文件与当前已关联在售商品集合不一致，拒绝写入")
    copies = {int(item["id"]): str(item.get("sales_copy") or "").strip() for item in records}
    if any(not value for value in copies.values()):
        raise ValueError("话术文件包含空话术，拒绝写入")
    with get_session_factory()() as session:
        rows = session.scalars(
            select(YouzanProductKnowledgeModel).where(
                YouzanProductKnowledgeModel.id.in_(copies)
            )
        ).all()
        for row in rows:
            row.sales_copy = copies[row.id]
        session.commit()
    print(json.dumps({"updated": len(copies)}, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(description="批量重写产品塑品话术")
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--export", metavar="PATH")
    actions.add_argument("--generate", metavar="PATH")
    actions.add_argument("--apply", metavar="PATH")
    parser.add_argument("--concurrency", type=int, default=4)
    args = parser.parse_args()
    if args.export:
        _export(args.export)
    elif args.generate:
        asyncio.run(_generate(args.generate, max(1, min(args.concurrency, 8))))
    else:
        _apply(args.apply)


if __name__ == "__main__":
    main()

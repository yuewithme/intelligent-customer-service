from dataclasses import dataclass
from typing import Any


VARIETY_SHEETS = {"建兰", "春兰", "春剑", "寒兰", "墨兰", "莲瓣兰", "蕙兰", "其他品类"}
COMMON_KNOWLEDGE_SHEET = "通用知识点"
SKU_SHEET = "链接详情"
SALES_COPY_SHEET = "私域运营团队2"
HOT_BREAKDOWN_SHEET = "热门品种拆解"


@dataclass
class OrchidImportPayload:
    categories: list[dict[str, Any]]
    varieties: list[dict[str, Any]]
    traits: list[dict[str, Any]]
    value_points: list[dict[str, Any]]
    skus: list[dict[str, Any]]
    common_knowledge: list[dict[str, Any]]
    sales_copy: list[dict[str, Any]]
    hot_breakdowns: list[dict[str, Any]]
    knowledge_chunks: list[dict[str, Any]]

    @property
    def counts(self) -> dict[str, int]:
        return {
            "categories": len(self.categories),
            "varieties": len(self.varieties),
            "traits": len(self.traits),
            "value_points": len(self.value_points),
            "skus": len(self.skus),
            "common_knowledge": len(self.common_knowledge),
            "sales_copy": len(self.sales_copy),
            "hot_breakdowns": len(self.hot_breakdowns),
            "knowledge_chunks": len(self.knowledge_chunks),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "counts": self.counts,
            "categories": self.categories,
            "varieties": self.varieties,
            "traits": self.traits,
            "value_points": self.value_points,
            "skus": self.skus,
            "common_knowledge": self.common_knowledge,
            "sales_copy": self.sales_copy,
            "hot_breakdowns": self.hot_breakdowns,
            "knowledge_chunks": self.knowledge_chunks,
        }


def read_records(sheet, required_headers: list[str]) -> list[dict[str, Any]]:
    rows = iter(sheet.iter_rows(values_only=True))
    for row in rows:
        headers = [cell_text(cell) for cell in row]
        if all(header in headers for header in required_headers):
            break
    else:
        return []

    records = []
    for row in rows:
        record = {
            header: row[index] if index < len(row) else None
            for index, header in enumerate(headers)
            if header
        }
        if any(cell_text(value) for value in record.values()):
            records.append(record)
    return records


def build_chunk(
    source_table: str,
    entity_type: str,
    variety_name: str | None,
    category_name: str | None,
    chunk_type: str,
    chunk_title: str,
    content: str,
) -> dict[str, Any]:
    return {
        "source_table": source_table,
        "source_id": None,
        "entity_type": entity_type,
        "variety_name": variety_name,
        "category_name": category_name,
        "chunk_type": chunk_type,
        "chunk_title": chunk_title,
        "content": content,
        "embedding_json": None,
    }


def cell_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).replace("\r\n", "\n").strip()

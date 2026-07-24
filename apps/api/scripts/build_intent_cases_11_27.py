from __future__ import annotations

import argparse
import asyncio
import json
import re
from pathlib import Path

from app.integrations.ai.services.llm_service import generate_json


API_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = API_ROOT.parents[1]
DEFAULT_SOURCE = PROJECT_ROOT / "docs" / "首单成交案例-意图识别清洗版.md"
OUTPUT_DIR = (
    API_ROOT
    / "app"
    / "domains"
    / "decisioning"
    / "data"
    / "intent_labeling_cases"
)
CASE_HEADING = re.compile(r"^###\s*案例(\d+)[：:]?\s*$")
VALID_ROLES = {"customer", "merchant"}
ROLE_CHUNK_SIZE = 10
ROLE_CONTEXT_SIZE = 8
SPLIT_MARKERS = {
    15: [
        "兰友您好！欢迎来到萧岚苑，我是养兰师傅：兰画，我们公司深耕国兰兰花培育、销售20多年",
    ],
    18: ["老师怎么送书？"],
    20: ["您回复“地区+阳台/室内”，我帮您选一盆适合您地区 好养的"],
}
ROLE_OVERRIDES = {
    ("case11", "还有盆墨兰，春兰暂时没有"): "customer",
    ("case14", "您是哪里"): "merchant",
    ("case14", "你是自己摸索着养兰花"): "merchant",
    ("case14", "发苗品相好的哟"): "customer",
    ("case15", "拍了，说声，给您发单品养护资料，邀请您进群"): "merchant",
    (
        "case20",
        "小白菜带花，您家有这个品种吗？这个品种好性价比又高",
    ): "merchant",
    ("case24", "带这个紫砂盆一套给我发吧。"): "customer",
    ("case24", "180+80原价260，给您打折实收249元。"): "merchant",
    ("case25", "红色花我有板桥，醉红素，汗血宝马"): "customer",
    ("case25", "奇花我有富山和梨山"): "customer",
    ("case25", "我付费即可hhh"): "merchant",
    ("case25", "到时开花了分享哈"): "merchant",
    ("case25", "整体很好，这露台的通风可以的"): "merchant",
}


def parse_cases(source: Path) -> dict[int, list[str]]:
    cases: dict[int, list[str]] = {}
    current_case: int | None = None
    for raw_line in source.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        heading = CASE_HEADING.match(line)
        if heading:
            current_case = int(heading.group(1))
            cases[current_case] = []
            continue
        if current_case is not None and line:
            cases[current_case].append(line)
    return cases


def split_case_conversations(
    case_number: int,
    messages: list[str],
) -> list[tuple[str, list[str]]]:
    boundaries = [0]
    for marker in SPLIT_MARKERS.get(case_number, []):
        boundary = next(
            (
                index
                for index, message in enumerate(messages)
                if message.startswith(marker)
            ),
            None,
        )
        if boundary is None:
            raise ValueError(f"case{case_number:02d}: split marker not found: {marker}")
        boundaries.append(boundary)
    boundaries.append(len(messages))
    boundaries = sorted(set(boundaries))
    segments = []
    for index, (start, end) in enumerate(
        zip(boundaries, boundaries[1:]),
        start=1,
    ):
        case_id = f"case{case_number:02d}" if index == 1 else f"case{case_number:02d}_{index}"
        segments.append((case_id, messages[start:end]))
    return segments


def build_role_prompt(
    case_number: int,
    messages: list[str],
    *,
    offset: int,
    previous_context: list[dict[str, str]],
) -> str:
    indexed_messages = [
        {"index": offset + index, "message": message}
        for index, message in enumerate(messages, start=1)
    ]
    return f"""
你正在整理一段兰花商家与客户的微信成交对话，用于意图识别评测。

请判断案例 {case_number} 中每一条消息的说话人：
- customer：客户发出的消息，包括客户发送图片、付款、地址等上下文说明。
- merchant：客服/商家发出的消息，包括商品介绍、养护建议、链接、资料等上下文说明。

要求：
1. 严格按输入顺序，为每条消息输出一个角色。
2. roles 数组长度必须等于本批待判断消息数 {len(messages)}。
3. 只能使用 "customer" 或 "merchant"。
4. 不得合并、删除或改写消息。
5. 根据完整上下文判断；连续多条消息可能属于同一个人。

只输出 JSON：
{{"roles":["customer","merchant"]}}

此前已确认的相邻上下文（只用于辅助判断，不要为这些消息输出角色）：
{json.dumps(previous_context, ensure_ascii=False)}

本批待判断消息：
{json.dumps(indexed_messages, ensure_ascii=False)}
""".strip()


def group_turns(messages: list[str], roles: list[str]) -> list[dict[str, object]]:
    turns: list[dict[str, object]] = []
    for message, role in zip(messages, roles, strict=True):
        if turns and turns[-1]["role"] == role:
            turns[-1]["messages"].append(message)
        else:
            turns.append({"role": role, "messages": [message]})
    return turns


async def classify_roles(
    case_number: int,
    case_id: str,
    messages: list[str],
) -> list[str]:
    all_roles: list[str] = []
    for offset in range(0, len(messages), ROLE_CHUNK_SIZE):
        chunk = messages[offset : offset + ROLE_CHUNK_SIZE]
        context_start = max(0, offset - ROLE_CONTEXT_SIZE)
        previous_context = [
            {
                "index": index + 1,
                "role": all_roles[index],
                "message": messages[index],
            }
            for index in range(context_start, offset)
        ]
        result = await generate_json(
            build_role_prompt(
                case_number,
                chunk,
                offset=offset,
                previous_context=previous_context,
            ),
            purpose="review",
            model_override="qwen3.7-plus",
            provider_override="dashscope",
            prompt_version="intent_case_role_segmentation_v1",
        )
        roles = result.get("roles")
        if not isinstance(roles, list) or len(roles) != len(chunk):
            raise ValueError(
                f"case{case_number:02d} offset {offset}: expected {len(chunk)} "
                f"roles, got "
                f"{len(roles) if isinstance(roles, list) else type(roles).__name__}"
            )
        normalized = []
        for message, role in zip(chunk, roles, strict=True):
            value = str(role).strip().lower()
            if value == "user":
                value = "customer"
            elif value == "assistant":
                value = "merchant"
            elif value == "system":
                if any(marker in message for marker in ("用户", "客户", "买家")):
                    value = "customer"
                elif any(
                    marker in message
                    for marker in ("商家", "客服", "发送", "展示", "商品卡片")
                ):
                    value = "merchant"
                else:
                    value = all_roles[-1] if all_roles else "merchant"
            normalized.append(value)
        invalid = sorted(set(normalized) - VALID_ROLES)
        if invalid:
            raise ValueError(
                f"case{case_number:02d} offset {offset}: invalid roles: {invalid}"
            )
        normalized = [
            ROLE_OVERRIDES.get((case_id, message), role)
            for message, role in zip(chunk, normalized, strict=True)
        ]
        all_roles.extend(normalized)
    return all_roles


async def build_cases(source: Path, case_numbers: list[int]) -> None:
    parsed = parse_cases(source)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for case_number in case_numbers:
        messages = parsed.get(case_number)
        if not messages:
            raise ValueError(f"case{case_number:02d} not found in {source}")
        for case_id, segment_messages in split_case_conversations(
            case_number, messages
        ):
            roles = await classify_roles(case_number, case_id, segment_messages)
            turns = group_turns(segment_messages, roles)
            payload = {
                "case_id": case_id,
                "customer_id": f"intent-{case_id}-customer",
                "source_file": "docs/首单成交案例-意图识别清洗版.md",
                "content_quality": "cleaned_verbatim_case_transcript",
                "turns": turns,
            }
            output = OUTPUT_DIR / f"{case_id}.json"
            output.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            customer_turns = sum(turn["role"] == "customer" for turn in turns)
            print(
                json.dumps(
                    {
                        "case_id": payload["case_id"],
                        "message_count": len(segment_messages),
                        "turn_count": len(turns),
                        "customer_turn_count": customer_turns,
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument(
        "--cases",
        nargs="+",
        type=int,
        default=list(range(11, 28)),
    )
    args = parser.parse_args()
    asyncio.run(build_cases(args.source, args.cases))


if __name__ == "__main__":
    main()

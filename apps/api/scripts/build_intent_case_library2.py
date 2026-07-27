from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


API_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = API_ROOT.parents[1]
DEFAULT_SOURCE = Path.home() / "Downloads" / "案例库2.md"
DEFAULT_CLEAN_OUTPUT = PROJECT_ROOT / "docs" / "案例库2-意图识别清洗版.md"
CASE_DATA_DIR = (
    API_ROOT
    / "app"
    / "domains"
    / "decisioning"
    / "data"
    / "intent_labeling_cases"
)

CASE_HEADING_RE = re.compile(r"^###\s*案例(?P<number>\d+)[：:]?\s*$")
TIMESTAMP_RE = re.compile(
    r"^(?P<sender>.+?)\s+"
    r"(?P<date>\d{1,2}/\d{1,2})\s+"
    r"(?P<time>\d{1,2}:\d{2}:\d{2})$"
)
MARKDOWN_LINK_RE = re.compile(r"^\[(?P<title>[^\]]+)]\((?P<url>https?://.+)\)$")
MARKDOWN_IMAGE_RE = re.compile(r"^!\[[^\]]*]\(https?://.+\)$")
RAW_URL_RE = re.compile(r"https?://\S+")
PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
LONG_NUMBER_RE = re.compile(r"(?<!\d)\d{12,}(?!\d)")
MARKDOWN_ESCAPE_RE = re.compile(r"\\([\\`*{}\[\]()#+\-.!_>~])")
QUOTED_REPLY_WITH_TAIL_RE = re.compile(
    r'^"(?P<quoted>.+?)"\s*\n?-{3,}\s*(?P<tail>.*)$',
    re.DOTALL,
)
QUOTED_REPLY_ONLY_RE = re.compile(r'^".+?"$', re.DOTALL)
PRODUCT_TITLE_RE = re.compile(r"【(?P<title>[^】]{2,40})】")

PROMOTION_KEYWORDS = (
    "原价",
    "福利价",
    "仅需",
    "放漏",
    "秒杀",
    "限时",
    "少量现货",
    "仅限",
    "首单朋友",
    "今日",
)


@dataclass
class Event:
    case_number: int
    role: str
    sender: str
    customer_key: str | None
    occurred_at: datetime
    content: str


def _is_sender_line(line: str) -> re.Match[str] | None:
    match = TIMESTAMP_RE.match(line)
    if match is None:
        return None
    sender = match.group("sender")
    if sender.startswith(("兰语", "兰隐")):
        return match
    if "@微信@微信联系人" in sender:
        return match
    return None


def _customer_key(sender: str) -> str:
    if "月月@微信" in sender:
        return "月月"
    if "洪淑@微信" in sender:
        return "洪淑"
    return sender.split("@微信@微信联系人", 1)[0].strip()


def parse_events(source: Path) -> dict[int, list[Event]]:
    cases: dict[int, list[Event]] = {}
    current_case: int | None = None
    current_meta: re.Match[str] | None = None
    content_lines: list[str] = []

    def flush() -> None:
        nonlocal current_meta, content_lines
        if current_case is None or current_meta is None:
            current_meta = None
            content_lines = []
            return
        sender = current_meta.group("sender").strip()
        role = "merchant" if sender.startswith(("兰语", "兰隐")) else "customer"
        content = "\n".join(line for line in content_lines if line.strip()).strip()
        if content:
            content = MARKDOWN_ESCAPE_RE.sub(r"\1", content)
            occurred_at = datetime.strptime(
                f"2026/{current_meta.group('date')} {current_meta.group('time')}",
                "%Y/%m/%d %H:%M:%S",
            )
            quoted_reply = (
                QUOTED_REPLY_WITH_TAIL_RE.match(content)
                if role == "customer"
                else None
            )
            if quoted_reply:
                quoted_content = re.sub(
                    r"^兰(?:语|隐)[^：]*：\s*",
                    "",
                    quoted_reply.group("quoted"),
                    count=1,
                ).strip()
                if quoted_content:
                    cases.setdefault(current_case, []).append(
                        Event(
                            case_number=current_case,
                            role="merchant",
                            sender="引用的客服消息",
                            customer_key=None,
                            occurred_at=occurred_at,
                            content=quoted_content,
                        )
                    )
                content = quoted_reply.group("tail").strip()
            if not content:
                current_meta = None
                content_lines = []
                return
            cases.setdefault(current_case, []).append(
                Event(
                    case_number=current_case,
                    role=role,
                    sender=sender,
                    customer_key=None if role == "merchant" else _customer_key(sender),
                    occurred_at=occurred_at,
                    content=content,
                )
            )
        current_meta = None
        content_lines = []

    for raw_line in source.read_text(encoding="utf-8").replace("\ufeff", "").splitlines():
        line = raw_line.strip()
        heading = CASE_HEADING_RE.match(line)
        if heading:
            flush()
            current_case = int(heading.group("number"))
            continue
        sender_match = _is_sender_line(line)
        if sender_match:
            flush()
            current_meta = sender_match
            continue
        if current_meta is not None:
            content_lines.append(line)
    flush()
    return cases


def split_customer_conversations(events: list[Event]) -> list[list[Event]]:
    customer_order: list[str] = []
    first_customer_indexes: dict[str, int] = {}
    for index, event in enumerate(events):
        if event.role != "customer" or event.customer_key is None:
            continue
        if event.customer_key not in first_customer_indexes:
            first_customer_indexes[event.customer_key] = index
            customer_order.append(event.customer_key)

    if len(customer_order) <= 1:
        return [events]

    boundaries = [0]
    for customer_key in customer_order[1:]:
        customer_index = first_customer_indexes[customer_key]
        customer_date = events[customer_index].occurred_at.date()
        boundary = customer_index
        while (
            boundary > boundaries[-1]
            and events[boundary - 1].role == "merchant"
            and events[boundary - 1].occurred_at.date() == customer_date
        ):
            boundary -= 1
        boundaries.append(boundary)
    boundaries.append(len(events))
    return [
        events[start:end]
        for start, end in zip(boundaries, boundaries[1:])
        if events[start:end]
    ]


def _media_placeholder(role: str, media_type: str) -> str:
    speaker = "客户" if role == "customer" else "客服"
    return f"（{speaker}发送{media_type}）"


def _looks_like_promotion(content: str) -> bool:
    keyword_count = sum(keyword in content for keyword in PROMOTION_KEYWORDS)
    if len(content) >= 80 and keyword_count >= 2:
        return True
    return (
        len(content) >= 150
        and keyword_count >= 1
        and PRODUCT_TITLE_RE.search(content) is not None
    )


def _strip_quoted_reply(content: str) -> str | None:
    match = QUOTED_REPLY_WITH_TAIL_RE.match(content)
    if match:
        return match.group("tail").strip() or None
    if QUOTED_REPLY_ONLY_RE.match(content):
        return None
    return content


def clean_content(event: Event) -> str | None:
    content = MARKDOWN_ESCAPE_RE.sub(r"\1", event.content).strip()
    content = _strip_quoted_reply(content)
    if content is None:
        return None
    if event.role == "customer":
        content = re.sub(
            r"^兰语\s+周一至周六9\.30-18\.30\s*",
            "",
            content,
        ).strip()
    if not content or content in {"❤", "，", "。"}:
        return None
    if "我已经添加了你，现在我们可以开始聊天了" in content:
        return None
    if MARKDOWN_IMAGE_RE.fullmatch(content):
        return _media_placeholder(event.role, "图片")
    if content in {"[视频]", "[图片]", "[自定义表情]"}:
        if event.role == "merchant" and content == "[自定义表情]":
            return None
        media_type = "视频" if "视频" in content else "图片"
        return _media_placeholder(event.role, media_type)

    link_match = MARKDOWN_LINK_RE.fullmatch(content)
    if link_match:
        title = link_match.group("title").strip()
        if "邀请你加入群聊" in title:
            return "（客服发送群聊邀请）"
        return f"（客服发送资料：{title}）"

    if RAW_URL_RE.search(content):
        content = RAW_URL_RE.sub(
            _media_placeholder(event.role, "链接"),
            content,
        )

    if _looks_like_promotion(content) and any(
        marker in content for marker in ("会员", "陪伴养兰", "视频教程")
    ):
        return "（客服发送商品或会员活动推荐，推广正文已清理）"

    product_title = PRODUCT_TITLE_RE.search(content)
    if (
        event.role == "merchant"
        and len(content) >= 120
        and product_title is not None
        and any(
            marker in content
            for marker in ("原价", "特价", "放漏", "现货", "花瓣", "花色", "株型")
        )
    ):
        return (
            f"（客服发送商品推荐：{product_title.group('title')}，"
            "推广正文已清理）"
        )

    if _looks_like_promotion(content):
        return "（客服发送商品或会员活动推荐，推广正文已清理）"

    if (
        event.role == "customer"
        and "省" in content
        and "市" in content
        and any(marker in content for marker in ("街道", "路", "号", "栋", "仓库"))
    ):
        return "（客户发送收货地址）"

    if PHONE_RE.search(content) or LONG_NUMBER_RE.search(content):
        if any(word in content for word in ("地址", "电话", "收货", "快递", "单号")):
            return _media_placeholder(event.role, "收货或物流信息")
        content = PHONE_RE.sub("[手机号]", content)
        content = LONG_NUMBER_RE.sub("[长数字]", content)

    content = re.sub(r"[ \t]+", " ", content)
    content = re.sub(r"\n{3,}", "\n\n", content)
    return content.strip() or None


def group_turns(events: list[Event]) -> list[dict[str, object]]:
    turns: list[dict[str, object]] = []
    for event in events:
        content = clean_content(event)
        if not content:
            continue
        if (
            turns
            and turns[-1]["role"] == event.role
            and turns[-1]["messages"][-1] == content
        ):
            continue
        if turns and turns[-1]["role"] == event.role:
            turns[-1]["messages"].append(content)
        else:
            turns.append({"role": event.role, "messages": [content]})
    return turns


def build_library(source: Path, clean_output: Path) -> list[dict[str, object]]:
    parsed = parse_events(source)
    payloads: list[dict[str, object]] = []
    clean_sections = ["# 案例库2（意图识别清洗版）"]

    for case_number in sorted(parsed):
        events = parsed[case_number]
        if not any(event.role == "customer" for event in events):
            continue
        conversations = split_customer_conversations(events)
        for part_number, conversation in enumerate(conversations, start=1):
            suffix = "" if part_number == 1 else f"_{part_number}"
            case_id = f"case2_{case_number:02d}{suffix}"
            turns = group_turns(conversation)
            if not any(turn["role"] == "customer" for turn in turns):
                continue
            payload = {
                "case_id": case_id,
                "customer_id": f"intent-{case_id}-customer",
                "source_file": "docs/案例库2-意图识别清洗版.md",
                "content_quality": "cleaned_verbatim_chat_export",
                "turns": turns,
            }
            payloads.append(payload)

            clean_sections.append(f"### {case_id}")
            for turn in turns:
                speaker = "客户" if turn["role"] == "customer" else "客服"
                for message in turn["messages"]:
                    clean_sections.append(f"{speaker}：{message}")

    clean_output.parent.mkdir(parents=True, exist_ok=True)
    clean_output.write_text("\n\n".join(clean_sections) + "\n", encoding="utf-8")
    CASE_DATA_DIR.mkdir(parents=True, exist_ok=True)
    for payload in payloads:
        output = CASE_DATA_DIR / f"{payload['case_id']}.json"
        output.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return payloads


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--clean-output", type=Path, default=DEFAULT_CLEAN_OUTPUT)
    args = parser.parse_args()
    payloads = build_library(args.source, args.clean_output)
    summary = []
    for payload in payloads:
        turns = payload["turns"]
        summary.append(
            {
                "case_id": payload["case_id"],
                "message_count": sum(len(turn["messages"]) for turn in turns),
                "customer_turn_count": sum(
                    turn["role"] == "customer" for turn in turns
                ),
            }
        )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

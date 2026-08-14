from __future__ import annotations

import hashlib
import re
from typing import Any


COPY_TYPES = {"话题种草", "养护科普", "名品故事"}

_CURATED_COPY: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (
        ("新手", "入门"),
        "话题种草",
        "新手养兰闭眼入 3 款，养不活算我的！你当初入坑第一盆兰花选的啥？评论聊聊～",
    ),
    (
        ("养兰花到底有什么意义", "兰花的寓意"),
        "话题种草",
        "中年人的治愈浪漫，不用红酒雪茄，案头一盆幽兰就够。你们家里主打什么铭品？",
    ),
    (
        ("老八种", "经典老种", "经典名品"),
        "话题种草",
        "懂兰的老藏家，家中必囤这几盆经典老种，说说你收藏的心头好是哪一款？",
    ),
    (
        ("晾根",),
        "养护科普",
        "上盆必须晾根，90% 兰友都忽略！你平时种兰花会晾根多久？",
    ),
    (
        ("舌形", "荷瓣", "梅瓣"),
        "养护科普",
        "兰花舌形直接决定品级，教你一眼分辨荷瓣 / 梅瓣，分得清自家兰的舌型吗？",
    ),
    (
        ("名贵兰花", "科技草", "下山草", "激素苗", "杂交苗"),
        "养护科普",
        "市面上名贵兰花怎么避坑？你踩过激素苗、杂交苗的坑吗？",
    ),
    (
        ("永怀素",),
        "名品故事",
        "莲瓣兰永怀素｜素花天花板的前世今生，有兰友养过这款白素吗？聊聊开花质感～",
    ),
    (
        ("素冠荷鼎",),
        "名品故事",
        "素冠荷鼎曾一苗天价，如今亲民好养，有没有兰友收藏这款传世名品？",
    ),
    (
        ("碧龙玉素",),
        "名品故事",
        "碧龙玉素莲瓣兰，通体如玉清雅脱俗，喜欢素心兰的兰友扣 1 交流！",
    ),
)

_CARE_KEYWORDS = (
    "养护",
    "上盆",
    "浇水",
    "施肥",
    "病害",
    "黄叶",
    "黑斑",
    "烂根",
    "腐苗",
    "度夏",
    "越冬",
    "注意",
    "禁忌",
    "植料",
    "芦头",
    "叶尖",
    "识别",
    "区分",
    "分辨",
    "图解",
    "对比",
    "结构",
    "晾根",
    "舌形",
    "瓣型",
    "光照",
    "耐寒",
    "春化",
    "不开花",
    "虫害",
    "症状",
    "深栽",
    "浅栽",
    "下花",
    "三伏天",
    "12个月",
    "种子能繁殖",
)

_TOPIC_KEYWORDS = ("一口气", "盘点", "排行榜", "最受欢迎")


def media_copy_for(*, title: str, category: str) -> dict[str, Any]:
    topic = clean_media_topic(title)
    for keywords, copy_type, copy_text in _CURATED_COPY:
        if any(keyword in topic for keyword in keywords):
            return {
                "copy_topic": topic,
                "copy_type": copy_type,
                "copy_text": copy_text,
                "copy_source": "curated",
            }

    if any(keyword in topic for keyword in _CARE_KEYWORDS):
        copy_type = "养护科普"
        copy_text = _care_copy(topic)
    elif category == "AI类" and not any(
        keyword in topic for keyword in _TOPIC_KEYWORDS
    ):
        copy_type = "名品故事"
        copy_text = (
            f"{topic}｜一盆兰花背后的品种故事和观赏看点，"
            "养过这款的兰友，聊聊它的开花表现～"
        )
    else:
        copy_type = "话题种草"
        copy_text = _topic_copy(topic)
    return {
        "copy_topic": topic,
        "copy_type": copy_type,
        "copy_text": copy_text,
        "copy_source": "template",
    }


def copy_ref_for(*, category: str, topic: str) -> str:
    digest = hashlib.sha256(f"{category}\0{topic}".encode()).hexdigest()
    return f"copy:{digest[:24]}"


def _care_copy(topic: str) -> str:
    if any(
        keyword in topic
        for keyword in ("病害", "虫害", "黄叶", "黑斑", "烂根", "腐苗", "症状")
    ):
        return (
            f"{topic}，早发现才能少走弯路。"
            "你遇到过哪种情况？评论区聊聊～"
        )
    if any(
        keyword in topic
        for keyword in ("图解", "结构", "瓣型", "叶形", "舌形", "名词")
    ):
        return (
            f"{topic}，一张图带你看明白。"
            "你能认出自家兰花属于哪一种吗？"
        )
    if any(
        keyword in topic for keyword in ("对比", "排名", "区分", "分辨", "识别")
    ):
        return (
            f"{topic}，不同品种的差别比想象中更大。"
            "你养的是哪一类？"
        )
    return (
        f"{topic}，这一步很多兰友容易忽略！"
        "你平时是怎么处理的？评论区聊聊～"
    )


def _topic_copy(topic: str) -> str:
    if any(keyword in topic for keyword in ("香", "香气")):
        return (
            f"{topic}，兰香各有性格。"
            "你最喜欢清香、幽香还是浓香？评论区聊聊～"
        )
    if any(
        keyword in topic
        for keyword in ("品系", "品种", "图鉴", "排名", "哪一种")
    ):
        return (
            f"{topic}，不同兰友心里的答案可能都不一样。"
            "你最喜欢哪一款？"
        )
    return (
        f"{topic}，懂兰的人越看越有味道。"
        "你家里有没有同类兰花？评论区聊聊～"
    )


def clean_media_topic(value: str) -> str:
    topic = re.sub(r"^\s*\d+[.、]?\s*", "", str(value or "").strip())
    topic = re.sub(r"(?<=[\u4e00-\u9fff])[1-4]$", "", topic)
    return topic or "兰花分享"

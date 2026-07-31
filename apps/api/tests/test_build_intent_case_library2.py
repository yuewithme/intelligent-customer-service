from datetime import datetime

from scripts.build_intent_case_library2 import (
    Event,
    _is_sender_line,
    _merge_clean_document,
    _turns_fingerprint,
    clean_content,
    parse_events,
)


def test_ruolan_sender_is_recognized_as_a_chat_sender():
    assert _is_sender_line(r"若兰🌿\(若兰\) 7/2 17:55:10") is not None


def test_additional_merchant_names_and_full_year_timestamps_are_recognized():
    assert _is_sender_line(r"兰亭\(兰亭\) 6/23 16:05:19") is not None
    assert _is_sender_line(r"兰香 \(兰香\) 2025/12/26 15:05:44") is not None


def test_full_year_timestamp_is_preserved_when_parsing(tmp_path):
    source = tmp_path / "case.md"
    source.write_text(
        "### 案例1\n\n"
        "兰香 \\(兰香\\) 2025/12/26 15:05:44\n\n您好\n\n"
        "客户@微信@微信联系人 2025/12/26 15:06:44\n\n多少钱\n",
        encoding="utf-8",
    )

    events = parse_events(source)[1]

    assert events[0].occurred_at == datetime(2025, 12, 26, 15, 5, 44)
    assert events[0].role == "merchant"
    assert events[1].role == "customer"


def test_new_clean_sections_merge_without_removing_or_duplicating_existing(tmp_path):
    output = tmp_path / "clean.md"
    output.write_text(
        "# 案例库2（意图识别清洗版）\n\n"
        "### case2_01\n\n客户：旧案例\n",
        encoding="utf-8",
    )
    merged = _merge_clean_document(
        output,
        "# 案例库2（意图识别清洗版）\n\n"
        "### case2_17\n\n客户：新案例\n",
    )
    output.write_text(merged, encoding="utf-8")
    repeated = _merge_clean_document(
        output,
        "# 案例库2（意图识别清洗版）\n\n"
        "### case2_17\n\n客户：新案例\n",
    )

    assert repeated.count("### case2_01") == 1
    assert repeated.count("### case2_17") == 1
    assert repeated.index("case2_01") < repeated.index("case2_17")


def test_long_marketing_broadcast_is_reduced_to_context_placeholder():
    content = clean_content(
        Event(
            case_number=17,
            role="merchant",
            sender="兰语",
            customer_key=None,
            occurred_at=datetime(2026, 7, 23, 14, 32, 42),
            content=(
                "❤名品寒兰【初恋｜烈焰红唇】实拍开品奉上！"
                "直立细叶飘逸耐看，价格亲民性价比拉满，收藏入门首选。"
                "很多兰友担心夏天高温难养活，咱们原盆原土带花苞发货。"
                "配专属度夏养护指南，高温季也能安稳服盆，喜欢红素别错过！"
            ),
        )
    )

    assert content == "（客服发送商品推荐：初恋｜烈焰红唇，推广正文已清理）"


def test_broadcast_with_reply_call_to_action_is_reduced_to_placeholder():
    content = clean_content(
        Event(
            case_number=1,
            role="merchant",
            sender="兰香",
            customer_key=None,
            occurred_at=datetime(2025, 12, 26, 15, 5, 44),
            content=(
                "下午好，看您一直关注兰花，年前想不想家里添一盆年宵花？"
                "今天强烈推荐一个特别适合新手的品种，花开稳定而且幽香。"
                "年前优选老种苗，根系壮实，正是结缘好时机。"
                "可以回复“666”，感兴趣结缘。"
            ),
        )
    )

    assert content == "（客服发送活动或养护内容，群发正文已清理）"


def test_turn_fingerprint_detects_an_exact_duplicate_conversation():
    original = [
        {"role": "merchant", "messages": ["您好"]},
        {"role": "customer", "messages": ["怎么养？", "需要每天浇水吗？"]},
    ]
    duplicate = [
        {"role": "merchant", "messages": ["您好"]},
        {"role": "customer", "messages": ["怎么养？", "需要每天浇水吗？"]},
    ]

    assert _turns_fingerprint(original) == _turns_fingerprint(duplicate)

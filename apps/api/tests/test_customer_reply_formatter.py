from app.domains.decisioning.services.customer_reply_formatter import plain_customer_text, split_customer_messages


def test_all_customer_text_removes_special_punctuation():
    text = "推荐您看看“芽黄素”（田黄玉）——花色清雅，适合阳台。"

    assert plain_customer_text(text) == "推荐您看看芽黄素田黄玉花色清雅，适合阳台。"


def test_long_reply_groups_every_two_sentences():
    sentences = [f"这是第{index}句，用于验证长回复会按两句合并发送。" for index in range(1, 6)]

    assert split_customer_messages("".join(sentences)) == [
        "".join(sentences[:2]),
        "".join(sentences[2:4]),
        sentences[4],
    ]


def test_blank_lines_preserve_semantic_message_boundaries():
    text = "已为您登记收货信息。\n\n请确认订单后，我会尽快安排发出。\n\n我这边持续跟进。"

    assert split_customer_messages(text) == ["已为您登记收货信息。请确认订单后，我会尽快安排发出。我这边持续跟进。"]


def test_short_fragment_with_comma_is_merged_into_one_reply():
    text = "你好，\n\n我在的。\n\n你可以直接问产品、价格、养护、发货或售后问题。"

    assert split_customer_messages(text) == ["你好，我在的。你可以直接问产品、价格、养护、发货或售后问题。"]


def test_long_semantic_reply_merges_fragment_ending_with_comma():
    from app.domains.decisioning.services.customer_reply_formatter import coalesce_customer_messages

    messages = coalesce_customer_messages(
        ["您好，", "第一段说明内容较长，用于模拟真实的客服服务说明。" * 3, "第二段继续说明内容。" * 4]
    )

    assert messages[0].startswith("您好，第一段")
    assert not any(message.endswith("，") for message in messages)


def test_exceptionally_long_sentence_splits_at_clause_boundary():
    clauses = ["这一小段内容用于测试超长句的自然切分，" for _ in range(12)]
    sentence = "".join(clauses) + "最后结束。"

    messages = split_customer_messages(sentence)

    assert len(messages) == 2
    assert "".join(messages) == sentence
    assert all(len(message) <= 200 for message in messages)

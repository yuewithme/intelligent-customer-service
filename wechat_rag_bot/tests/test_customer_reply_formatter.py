from app.services.customer_reply_formatter import split_customer_messages


def test_long_reply_groups_every_two_sentences():
    sentences = [f"这是第{index}句，用于验证长回复会按两句合并发送。" for index in range(1, 6)]

    assert split_customer_messages("".join(sentences)) == [
        "".join(sentences[:2]),
        "".join(sentences[2:4]),
        sentences[4],
    ]


def test_blank_lines_preserve_semantic_message_boundaries():
    text = "已为您登记收货信息。\n\n请确认订单后，我会尽快安排发出。\n\n我这边持续跟进。"

    assert split_customer_messages(text) == [
        "已为您登记收货信息。",
        "请确认订单后，我会尽快安排发出。",
        "我这边持续跟进。",
    ]


def test_exceptionally_long_sentence_splits_at_clause_boundary():
    clauses = ["这一小段内容用于测试超长句的自然切分，" for _ in range(12)]
    sentence = "".join(clauses) + "最后结束。"

    messages = split_customer_messages(sentence)

    assert len(messages) == 2
    assert "".join(messages) == sentence
    assert all(len(message) <= 200 for message in messages)

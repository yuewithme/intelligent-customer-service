from app.utils.time import now_iso


_examples: list[dict] = [
    {
        "example_id": "ex_price_default",
        "text": "有点贵，我再考虑一下",
        "route": "template_reply",
        "primary_intent": "price_objection",
        "secondary_intents": ["hesitation"],
        "sales_stage": "objection_handling",
        "created_at": now_iso(),
    }
]


async def add_intent_example(example: dict) -> dict:
    item = dict(example)
    item.setdefault("created_at", now_iso())
    _examples[:] = [
        old for old in _examples if old.get("example_id") != item.get("example_id")
    ]
    _examples.append(item)
    return item


async def retrieve_intent_examples(message: str, top_k: int = 5) -> list[dict]:
    text = message.strip()
    scored = []
    for example in _examples:
        sample = example.get("text", "")
        overlap = sum(1 for char in set(text) if char in sample)
        keyword_bonus = 3 if any(word in text for word in ("贵", "价格", "优惠")) else 0
        scored.append((overlap + keyword_bonus, example))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [example for score, example in scored[:top_k] if score > 0]

from app.schemas.tag import TagResult


def test_tag_result_defaults_are_stable():
    result = TagResult(
        intent="orchid_care",
        route="rag_answer",
        segment="beginner",
        confidence=0.88,
    )

    assert result.intent == "orchid_care"
    assert result.route == "rag_answer"
    assert result.segment == "beginner"
    assert result.emotion == "neutral"
    assert result.stage == "unknown"
    assert result.risk_level == "normal"
    assert result.entities == {}
    assert result.tags == ["intent:orchid_care", "segment:beginner"]

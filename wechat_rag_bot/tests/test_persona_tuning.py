from evaluation.persona_tuning import (
    calculate_judge_score,
    evaluate_answer,
    parse_judge_content,
    recompute_rows,
    summarize,
    summarize_judgments,
)


def test_evaluate_answer_detects_identity_and_style_violations():
    case = {
        "identity_case": True,
        "question_expected": True,
        "max_chars": 100,
        "must_avoid": ["真人销售"],
    }

    violations = evaluate_answer(
        case,
        "亲亲，我是真人销售，不是机器人。您想买什么？还需要什么？",
        {},
    )

    assert "multiple_questions" in violations
    assert "customer_service_tone" in violations
    assert "false_human_claim" in violations
    assert "machine_identity_detour" in violations
    assert "missing_role_redirect" in violations
    assert "must_avoid:真人销售" in violations


def test_summarize_reports_clean_and_identity_rates():
    rows = [
        {"identity_case": True, "violations": []},
        {"identity_case": True, "violations": ["false_human_claim"]},
        {"identity_case": False, "violations": []},
    ]

    summary = summarize(rows)

    assert summary["total"] == 3
    assert summary["clean_rate"] == 0.6667
    assert summary["identity_clean_rate"] == 0.5
    assert summary["violation_counts"] == {"false_human_claim": 1}


def test_recompute_rows_applies_current_metrics_to_saved_answers():
    cases = [{"id": "c1", "question_expected": False}]
    rows = [{"id": "c1", "answer": "你把手机号发我。", "violations": []}]

    updated = recompute_rows(cases, rows)

    assert updated[0]["violations"] == ["unnecessary_question"]


def test_persona_judge_parsing_and_summary():
    judgment = parse_judge_content(
        """```json
{
  "naturalness": 18,
  "persona_consistency": 19,
  "goal_completion": 20,
  "question_discipline": 15,
  "safety_boundary": 15,
  "wechat_concision": 9,
  "violations": [],
  "reason": "自然"
}
```"""
    )

    score = calculate_judge_score(judgment)
    summary = summarize_judgments(
        [{"score": score}, {"score": 75}]
    )

    assert score == 96
    assert summary == {
        "count": 2,
        "average_score": 85.5,
        "minimum_score": 75.0,
        "scores_below_80": 1,
    }

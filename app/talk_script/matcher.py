from app.db.models import QuestionClusterModel, SceneIndexModel
from app.talk_script.models import CandidateQuestion
from app.talk_script.normalizer import normalize_message


HIGH_RISK_WORDS = ("退款", "投诉", "苗坏", "死了", "烂根", "发黑", "快死", "状态不好")
PRICE_WORDS = ("多少钱", "什么价", "价格", "太贵", "便宜", "优惠", "别人家便宜")
RECOMMEND_WORDS = ("推荐", "适合", "新手", "想买", "阳台", "香", "红花")
CARE_FAQ_WORDS = ("浇水", "怎么养", "换盆", "植料", "夏天", "冬天", "施肥")
GREET_WORDS = ("你好", "您好", "在吗", "想了解", "资料")


def match_scene(
    message: str,
    scenes: list[SceneIndexModel],
    recent_messages: list[str] | None = None,
) -> str | None:
    del recent_messages
    text = normalize_message(message)
    if not text or not scenes:
        return None

    forced = _forced_scene(text)
    if forced and any(scene.scene_id == forced for scene in scenes):
        return forced

    scored: list[tuple[int, int, str]] = []
    for scene in scenes:
        excluded = _contains_any(text, _split_terms(scene.exclude_conditions))
        if excluded:
            continue
        terms = []
        terms.extend(_split_terms(scene.typical_user_messages))
        terms.extend(_split_terms(scene.enter_conditions))
        terms.extend(_split_terms(scene.scene_definition))
        score = sum(_term_score(text, term) for term in terms)
        if score > 0:
            scored.append((score, scene.priority, scene.scene_id))
    if not scored:
        return None
    scored.sort(reverse=True)
    return scored[0][2]


def retrieve_candidate_questions(
    *,
    normalized_message: str,
    scene_id: str,
    questions: list[QuestionClusterModel],
    limit: int = 5,
) -> list[CandidateQuestion]:
    scored: list[tuple[float, int, QuestionClusterModel]] = []
    for question in questions:
        if question.scene_id != scene_id or question.status != "active":
            continue
        if _contains_any(normalized_message, _split_terms(question.exclude_conditions)):
            continue
        if _contains_any(normalized_message, _split_terms(question.negative_examples)):
            continue
        score = _question_score(normalized_message, question)
        if score <= 0:
            continue
        scored.append((score, question.priority, question))
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [_to_candidate(question) for _, _, question in scored[:limit]]


def _forced_scene(text: str) -> str | None:
    if _contains_any(text, HIGH_RISK_WORDS):
        return "S05"
    if _contains_any(text, PRICE_WORDS):
        return "S07"
    if _contains_any(text, RECOMMEND_WORDS):
        return "S06"
    if _contains_any(text, CARE_FAQ_WORDS):
        return "S04"
    if _contains_any(text, GREET_WORDS):
        return "S01"
    return None


def _question_score(text: str, question: QuestionClusterModel) -> float:
    score = 0.0
    for term in _split_terms(question.keywords):
        score += _term_score(text, term) * 3
    for term in _split_terms(question.user_question_aliases):
        score += _term_score(text, term) * 2
    for term in _split_terms(question.positive_examples):
        score += _term_score(text, term) * 2
    for field in (question.standard_question, question.core_intent):
        if field:
            overlap = len(set(text) & set(field)) / max(len(set(field)), 1)
            score += overlap
    return score


def _term_score(text: str, term: str) -> float:
    if not term:
        return 0.0
    if term in text:
        return 1.0
    overlap = len(set(text) & set(term)) / max(len(set(term)), 1)
    return overlap if overlap >= 0.6 and len(term) >= 3 else 0.0


def _contains_any(text: str, terms) -> bool:
    return any(term and term in text for term in terms)


def _split_terms(value: str | None):
    if not value:
        return []
    normalized = normalize_message(value)
    parts = []
    for separator in ("｜", "|", "/", "、", ",", ";", "\n"):
        normalized = normalized.replace(separator, "|")
    for part in normalized.split("|"):
        term = part.strip()
        if term:
            parts.append(term)
    return parts


def _to_candidate(question: QuestionClusterModel) -> CandidateQuestion:
    return CandidateQuestion(
        question_id=question.question_id,
        scene_id=question.scene_id,
        sub_scene_name=question.sub_scene_name,
        standard_question=question.standard_question,
        core_intent=question.core_intent,
        positive_examples=question.positive_examples,
        negative_examples=question.negative_examples,
        required_conditions=question.required_conditions,
        exclude_conditions=question.exclude_conditions,
        confidence_threshold=question.confidence_threshold,
        priority=question.priority,
    )

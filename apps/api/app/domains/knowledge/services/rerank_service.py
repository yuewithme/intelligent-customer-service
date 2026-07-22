import re
from typing import Any


VECTOR_WEIGHT = 0.35
KEYWORD_WEIGHT = 0.45
SECTION_WEIGHT = 0.15
FILE_NAME_WEIGHT = 0.03
EXACT_PHRASE_WEIGHT = 0.02


async def rerank(
    question: str,
    docs: list[dict[str, Any]],
    top_n: int,
) -> list[dict[str, Any]]:
    query_terms = _query_terms(question)
    scored_docs = [_with_rerank_score(doc, query_terms, question) for doc in docs]
    return sorted(
        scored_docs,
        key=lambda doc: (doc.get("rerank_score") or 0.0, doc.get("score") or 0.0),
        reverse=True,
    )[:top_n]


def _with_rerank_score(
    doc: dict[str, Any],
    query_terms: set[str],
    question: str,
) -> dict[str, Any]:
    text = str(doc.get("text") or "")
    section = str(doc.get("section") or "")
    file_name = str(doc.get("file_name") or "")
    vector_score = _normalize_vector_score(doc.get("score"))
    keyword_score = _term_overlap_score(query_terms, text)
    section_score = _term_overlap_score(query_terms, section)
    file_name_score = _term_overlap_score(query_terms, file_name)
    exact_phrase_score = _exact_phrase_score(question, text, section, file_name)
    rerank_score = (
        vector_score * VECTOR_WEIGHT
        + keyword_score * KEYWORD_WEIGHT
        + section_score * SECTION_WEIGHT
        + file_name_score * FILE_NAME_WEIGHT
        + exact_phrase_score * EXACT_PHRASE_WEIGHT
    )
    return {
        **doc,
        "rerank_score": round(rerank_score, 6),
        "rerank_reason": {
            "vector_score": round(vector_score, 6),
            "keyword_score": round(keyword_score, 6),
            "section_score": round(section_score, 6),
            "file_name_score": round(file_name_score, 6),
            "exact_phrase_score": round(exact_phrase_score, 6),
        },
    }


def _normalize_vector_score(value: Any) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(score, 1.0))


def _query_terms(question: str) -> set[str]:
    normalized = question.lower()
    terms = set(re.findall(r"[a-z0-9]+", normalized))
    cjk_chars = re.findall(r"[\u4e00-\u9fff]", normalized)
    terms.update(cjk_chars)
    terms.update(
        "".join(cjk_chars[index : index + 2])
        for index in range(0, max(0, len(cjk_chars) - 1))
    )
    terms.update(
        "".join(cjk_chars[index : index + 3])
        for index in range(0, max(0, len(cjk_chars) - 2))
    )
    return {term for term in terms if term.strip()}


def _term_overlap_score(query_terms: set[str], value: str) -> float:
    if not query_terms or not value:
        return 0.0
    normalized = value.lower()
    matched = sum(1 for term in query_terms if term and term in normalized)
    return min(1.0, matched / max(len(query_terms), 1))

def _exact_phrase_score(question: str, *values: str) -> float:
    normalized_question = question.strip().lower()
    if not normalized_question:
        return 0.0
    compact_question = re.sub(r"\s+", "", normalized_question)
    if not compact_question:
        return 0.0
    for value in values:
        compact_value = re.sub(r"\s+", "", str(value or "").lower())
        if compact_question in compact_value:
            return 1.0
    return 0.0

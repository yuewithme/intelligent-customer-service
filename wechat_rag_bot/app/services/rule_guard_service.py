from app.schemas.common import AppError, ErrorCode
from app.schemas.event import NormalizedMessage
from app.schemas.intent import IntentResult
from app.schemas.state import UserState


def _contains_any(text: str, words: tuple[str, ...]) -> bool:
    return any(word in text for word in words)


async def check_rules(
    message: NormalizedMessage,
    user_state: UserState,
) -> IntentResult | None:
    del user_state
    text = message.message.strip()
    if not text:
        raise AppError(ErrorCode.MESSAGE_EMPTY)

    if _contains_any(
        text,
        ("转人工", "找人工", "人工客服", "真人客服", "转客服", "找客服", "客服介入"),
    ):
        return IntentResult(
            route="human",
            primary_intent="human_request",
            primary_domain="customer_service",
            primary_goal="request_human",
            classifier_source="rule_guard",
            confidence=0.98,
            need_human=True,
            reason="rule_guard_human_request",
        )
    if _contains_any(text, ("退款", "退货")):
        return IntentResult(
            route="human",
            primary_intent="refund_request",
            primary_domain="customer_service",
            primary_goal="request_refund_return",
            issues=["refund_return"],
            classifier_source="rule_guard",
            confidence=0.98,
            need_human=True,
            reason="rule_guard_refund",
        )
    if _contains_any(text, ("投诉", "举报", "骗子", "骗我", "不满意")):
        return IntentResult(
            route="human",
            primary_intent="complaint",
            primary_domain="customer_service",
            primary_goal="complain",
            classifier_source="rule_guard",
            confidence=0.98,
            need_human=True,
            reason="rule_guard_complaint",
        )
    return None

from app.talk_script.models import HumanHandoffResult


async def request_human_handoff(
    *,
    customer_id: str,
    current_message: str,
    reason: str,
    context: dict,
) -> HumanHandoffResult:
    del current_message
    return HumanHandoffResult(
        requested=True,
        status="pending",
        reason=reason,
        metadata={"customer_id": customer_id, "context": context},
    )

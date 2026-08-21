from app.domains.conversations.services.chat_orchestrator import _customer_workspace
from app.domains.customers.schemas.state import UserState


class _Message:
    metadata = {}
    user_id = "customer-1"


def test_customer_workspace_does_not_expose_profile_names_to_agent():
    workspace = _customer_workspace(
        message=_Message(),
        user_state=UserState(user_id="customer-1"),
        profile_bundle={
            "profile": {
                "basic_info": {
                    "nickname": "贵杰",
                    "remark_name": "黄先生",
                    "display_name": "奇怪微信名",
                    "region": "杭州",
                }
            },
            "recent_memories": [],
        },
    )

    assert workspace["profile"]["basic_info"] == {"region": "杭州"}

import secrets
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from app.config import get_settings
from app.services.demo_sales_agent_service import chat_with_demo_sales_agent


settings = get_settings()

sales_mcp = FastMCP(
    "Sales Agent Demo",
    instructions=(
        "与销售 Agent 进行真实的多轮客户对话。首次调用可省略 conversation_id，"
        "后续调用应传回服务端返回的 conversation_id。"
    ),
    stateless_http=True,
    json_response=True,
    streamable_http_path="/mcp",
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=settings.mcp_allowed_hosts,
        allowed_origins=settings.mcp_allowed_origins,
    ),
)


@sales_mcp.tool()
async def chat_with_sales_agent(
    customer_id: str,
    message: str,
    conversation_id: str | None = None,
    customer_name: str | None = None,
) -> dict[str, Any]:
    """以客户身份和销售 Agent 对话，支持知识问答、推荐、画像和销售推进。"""
    return await chat_with_demo_sales_agent(
        channel="mcp_demo",
        customer_id=customer_id,
        conversation_id=conversation_id,
        message=message,
        customer_name=customer_name,
    )


class MCPBearerAuth:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http" and scope.get("path") == "/mcp":
            settings = get_settings()
            if not settings.api_auth_enabled:
                await self.app(scope, receive, send)
                return
            headers = {key.lower(): value for key, value in scope.get("headers", [])}
            authorization = headers.get(b"authorization", b"").decode("latin-1")
            expected = settings.mcp_api_key or settings.api_key
            valid = authorization.startswith("Bearer ") and secrets.compare_digest(
                authorization[7:], expected
            )
            if not valid:
                await send(
                    {
                        "type": "http.response.start",
                        "status": 401,
                        "headers": [
                            (b"content-type", b"application/json"),
                            (b"www-authenticate", b"Bearer"),
                        ],
                    }
                )
                await send(
                    {
                        "type": "http.response.body",
                        "body": b'{"error":"unauthorized"}',
                    }
                )
                return
        await self.app(scope, receive, send)


mcp_asgi_app = MCPBearerAuth(sales_mcp.streamable_http_app())

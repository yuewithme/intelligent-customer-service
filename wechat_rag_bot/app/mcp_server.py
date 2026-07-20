import secrets
from contextlib import asynccontextmanager
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from app.config import get_settings
from app.services.demo_sales_agent_service import chat_with_demo_sales_agent
from app.services.youzan_ai_tool_service import YouzanAIToolService


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


@sales_mcp.tool()
async def youzan_search_products(keyword: str, limit: int = 3) -> dict[str, Any]:
    """只读搜索有赞在售商品。limit 范围 1-10，不执行商品或库存写操作。"""
    return await YouzanAIToolService.from_settings().search_products(
        keyword=keyword,
        limit=limit,
    )


@sales_mcp.tool()
async def youzan_get_product(item_id: str) -> dict[str, Any]:
    """只读获取一个有赞商品详情，不执行商品写操作。"""
    return await YouzanAIToolService.from_settings().get_product(item_id=item_id)


@sales_mcp.tool()
async def youzan_list_inventory(limit: int = 20) -> dict[str, Any]:
    """只读查询有赞库存列表。limit 范围 1-50，不执行库存调整。"""
    return await YouzanAIToolService.from_settings().list_inventory(limit=limit)


@sales_mcp.tool()
async def youzan_resolve_customer(
    customer_id: str,
    mobile: str,
    tenant_id: str = "tenant_default",
    channel: str = "wechat",
) -> dict[str, Any]:
    """用手机号只读识别当前客户并持久绑定；手机号完整落库，但绝不返回给 AI。"""
    return await YouzanAIToolService.from_settings().resolve_customer(
        customer_id=customer_id,
        mobile=mobile,
        tenant_id=tenant_id,
        channel=channel,
    )


@sales_mcp.tool()
async def youzan_search_customer_orders(
    customer_id: str,
    mobile: str | None = None,
    limit: int = 3,
    tenant_id: str = "tenant_default",
    channel: str = "wechat",
) -> dict[str, Any]:
    """只读查询当前客户订单；首次需手机号，之后复用已验证的客户绑定。"""
    return await YouzanAIToolService.from_settings().search_customer_orders(
        customer_id=customer_id,
        mobile=mobile,
        limit=limit,
        tenant_id=tenant_id,
        channel=channel,
    )


@sales_mcp.tool()
async def youzan_get_customer_order(
    customer_id: str,
    order_no: str,
    mobile: str | None = None,
    tenant_id: str = "tenant_default",
    channel: str = "wechat",
) -> dict[str, Any]:
    """只读获取当前客户的订单详情；先验证订单归属，再调用有赞详情接口。"""
    return await YouzanAIToolService.from_settings().get_customer_order(
        customer_id=customer_id,
        order_no=order_no,
        mobile=mobile,
        tenant_id=tenant_id,
        channel=channel,
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


@asynccontextmanager
async def run_sales_mcp_session_manager():
    """Run the MCP manager across repeatable application lifespans.

    The upstream manager is one-shot, while tests and embedded deployments may
    create the FastAPI lifespan more than once in the same process.
    """
    manager = sales_mcp.session_manager
    if getattr(manager, "_has_started", False) and getattr(manager, "_task_group", None) is None:
        manager._has_started = False
    try:
        async with manager.run():
            yield
    finally:
        if getattr(manager, "_task_group", None) is None:
            manager._has_started = False

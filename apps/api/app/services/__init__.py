"""Lazy compatibility imports for legacy tests and operational scripts.

Application code must import services from ``app.domains`` or
``app.integrations`` directly. This package can be removed after downstream
callers have migrated.
"""

from importlib import import_module
from types import ModuleType


_DOMAIN_GROUPS = {
    "conversations": (
        "state_service",
        "conversation_service",
        "conversation_event_service",
        "chat_orchestrator",
        "chat_log_service",
        "channel_service",
    ),
    "customers": (
        "user_profile_service",
        "customer_level_service",
        "memory_vector_service",
        "memory_validation_service",
        "memory_rollout_service",
        "memory_retrieval_service",
        "memory_rerank_service",
        "memory_repository",
        "memory_query_planner",
        "memory_projection_service",
        "memory_procedure_service",
        "memory_lifecycle_service",
        "memory_job_service",
        "memory_identity_service",
        "memory_extraction_service",
        "memory_event_service",
        "memory_dual_write_service",
        "memory_consolidation_service",
    ),
    "sales": (
        "activity_service",
        "admin_tag_service",
        "business_tag_prompt_service",
        "care_manual_service",
        "sales_stage_catalog",
        "shipping_contact_service",
        "tag_catalog",
        "contact_sync_service",
        "daily_touch_service",
    ),
    "catalog": (
        "orchid_material_service",
        "product_knowledge_service",
        "product_sales_copy_service",
    ),
    "knowledge": (
        "context_selector",
        "embedding_service",
        "knowledge_service",
        "qdrant_service",
        "rag_service",
        "rag_debug_service",
        "rerank_service",
    ),
    "decisioning": (
        "customer_reply_formatter",
        "demo_sales_agent_service",
        "persona_service",
        "policy_engine",
        "prompt_builder",
        "agent_prompt",
        "agent_runtime",
        "agent_tools",
    ),
    "handoff": ("handoff_notification_service",),
}
_INTEGRATION_GROUPS = {
    "eyun": (
        "eyun_material_service",
        "eyun_contact_service",
        "eyun_callback_service",
        "message_risk_control_service",
    ),
    "wechat": ("wechat_service",),
    "youzan": (
        "youzan_product_sync_service",
        "youzan_product_service",
        "youzan_order_service",
        "youzan_identity_store",
        "youzan_callback_service",
        "youzan_ai_tool_service",
    ),
    "ai": ("llm_service", "vision_service"),
    "web": ("link_card_thumbnail_service",),
}
_MODULE_PATHS = {
    name: f"app.domains.{domain}.services.{name}"
    for domain, names in _DOMAIN_GROUPS.items()
    for name in names
} | {
    name: f"app.integrations.{provider}.services.{name}"
    for provider, names in _INTEGRATION_GROUPS.items()
    for name in names
}
__all__ = sorted(_MODULE_PATHS)


def __getattr__(name: str) -> ModuleType:
    path = _MODULE_PATHS.get(name)
    if path is None:
        raise AttributeError(name)
    module = import_module(path)
    globals()[name] = module
    return module

"""Lazy compatibility imports for router modules.

New application code registers routers through ``app.bootstrap.routes``.
"""

from importlib import import_module
from types import ModuleType


_DOMAIN_GROUPS = {
    "conversations": (
        "admin_conversations",
        "admin_logs",
        "chat",
        "demo",
        "demo_admin",
    ),
    "customers": ("admin_memory", "state", "user_profile"),
    "sales": (
        "admin_activities",
        "admin_care_manuals",
        "admin_tags",
    ),
    "catalog": ("admin_products",),
    "knowledge": ("knowledge",),
    "decisioning": (
        "admin_conversation_cases",
    ),
    "handoff": ("admin_handoff_notification",),
    "access": ("admin_gate",),
}
_INTEGRATION_GROUPS = {
    "eyun": ("admin_eyun_materials", "eyun"),
    "wechat": ("wechat",),
    "youzan": ("youzan",),
}
_MODULE_PATHS = {
    name: f"app.domains.{domain}.api.{name}"
    for domain, names in _DOMAIN_GROUPS.items()
    for name in names
} | {
    name: f"app.integrations.{provider}.api.{name}"
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

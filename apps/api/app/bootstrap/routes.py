from fastapi import FastAPI

from app.domains.access.api import admin_gate
from app.domains.catalog.api import admin_products
from app.domains.conversations.api import (
    admin_conversations,
    admin_logs,
    chat,
    demo,
    demo_admin,
)
from app.domains.customers.api import admin_memory, state, user_profile
from app.domains.decisioning.api import (
    admin_intent_observations,
    debug,
    intent_examples,
    templates,
)
from app.domains.handoff.api import admin_handoff_notification
from app.domains.knowledge.api import knowledge
from app.domains.sales.api import (
    admin_activities,
    admin_care_manuals,
    admin_sales_flow,
    admin_service_sop,
    admin_tags,
    admin_unpurchased_sop,
)
from app.integrations.eyun.api import admin_eyun_materials, eyun
from app.integrations.wechat.api import wechat
from app.integrations.youzan.api import youzan


ROUTERS = (
    admin_activities.router,
    admin_care_manuals.router,
    admin_conversations.router,
    admin_eyun_materials.router,
    admin_gate.router,
    admin_handoff_notification.router,
    admin_intent_observations.router,
    admin_tags.router,
    chat.router,
    knowledge.router,
    templates.router,
    intent_examples.router,
    user_profile.router,
    state.router,
    debug.router,
    demo.router,
    demo_admin.router,
    demo_admin.profile_router,
    admin_logs.router,
    admin_memory.router,
    admin_products.router,
    admin_sales_flow.router,
    admin_unpurchased_sop.router,
    admin_service_sop.router,
    wechat.router,
    eyun.router,
    youzan.router,
)


def register_routes(app: FastAPI) -> None:
    for router in ROUTERS:
        app.include_router(router)

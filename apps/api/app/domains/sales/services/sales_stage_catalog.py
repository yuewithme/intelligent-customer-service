from app.domains.sales.schemas.sales_flow import (
    CustomerSignal,
    SalesAction,
    SalesInterruptionType,
    SalesKnowledgeSource,
    SalesStage,
    SalesStageDefinition,
    SalesStageNormalization,
)


COMMON_KNOWLEDGE_SOURCES = (
    SalesKnowledgeSource.CUSTOMER_CONTEXT,
    SalesKnowledgeSource.CARE_SAFE,
    SalesKnowledgeSource.STAGE_SCRIPT,
)


SALES_STAGE_DEFINITIONS: tuple[SalesStageDefinition, ...] = (
    SalesStageDefinition(
        stage=SalesStage.RAPPORT,
        display_name="破冰",
        sequence=1,
        objective="让客户开口并建立自然、可信的沟通",
        entry_evidence_any=["new_first_order_opportunity", "responded"],
        exit_evidence_any=["responded", "service_need", "product_need", "combined_need"],
        allowed_actions=[SalesAction.ANSWER_CURRENT_QUESTION, SalesAction.BUILD_RAPPORT],
        allowed_knowledge_sources=COMMON_KNOWLEDGE_SOURCES,
        conditional_knowledge_sources=[SalesKnowledgeSource.PRODUCT_CATALOG],
        required_slot_groups=[],
        prohibited_behaviors=["连续抛出多个问题", "未了解来意就立即硬推产品"],
    ),
    SalesStageDefinition(
        stage=SalesStage.NEED_DISCOVERY,
        display_name="挖需求",
        sequence=2,
        objective=(
            "围绕养兰困难引导客户说出具体痛点；首单默认优先匹配陪伴养兰服务，"
            "客户明确表达购兰需求时再进入产品方向"
        ),
        entry_evidence_any=["responded", "service_need", "product_need", "combined_need", "price_interest"],
        exit_evidence_any=["need_track", "service_need", "product_need", "combined_need"],
        allowed_actions=[
            SalesAction.ANSWER_CURRENT_QUESTION,
            SalesAction.DISCOVER_NEED_TRACK,
            SalesAction.DISCOVER_PAIN,
        ],
        allowed_knowledge_sources=COMMON_KNOWLEDGE_SOURCES,
        conditional_knowledge_sources=[SalesKnowledgeSource.PRODUCT_CATALOG],
        required_slot_groups=[
            ["pain_point"],
            ["desired_outcome"],
            ["failed_history"],
        ],
        prohibited_behaviors=[
            "未回答客户当前问题就开始问卷",
            "重复询问已经明确或客户未回答的问题",
            "用服务还是产品、想达到什么效果等空泛二选一问题挖需",
            "客户没有明确购兰需求时优先推荐兰花",
        ],
    ),
    SalesStageDefinition(
        stage=SalesStage.PAIN_DISCOVERY,
        display_name="确认需求",
        sequence=3,
        objective=(
            "用具体例子引导客户说出核心养兰需求，包括当前问题、想达到的结果或过往失败经历；"
            "需求明确后停止横向追问，先针对需求作答，再推进陪伴养兰服务"
        ),
        entry_evidence_any=["pain_revealed", "desired_outcome", "product_need", "combined_need"],
        exit_evidence_any=[
            "pain_point",
            "desired_outcome",
            "failed_history",
            "selected_product_id",
        ],
        allowed_actions=[SalesAction.ANSWER_CURRENT_QUESTION, SalesAction.DISCOVER_PAIN],
        allowed_knowledge_sources=COMMON_KNOWLEDGE_SOURCES,
        conditional_knowledge_sources=[SalesKnowledgeSource.PRODUCT_CATALOG],
        required_slot_groups=[
            ["pain_point"],
            ["desired_outcome"],
            ["failed_history"],
        ],
        prohibited_behaviors=[
            "夸大客户损失",
            "在证据不足时给出确定性诊断",
            "客户已关注具体痛点后继续追问其他养护方向",
            "追着客户未回答的单一诊断问题反复询问",
        ],
    ),
    SalesStageDefinition(
        stage=SalesStage.SOLUTION_RECOMMENDED,
        display_name="推品",
        sequence=4,
        objective=(
            "首单优先推荐能解决养兰痛点的陪伴服务；只有客户明确表达购兰或选品需求时，"
            "才推荐可解释的兰花方案"
        ),
        entry_evidence_any=["recommendation_ready", "selected_product_id", "preference_revealed"],
        exit_evidence_any=["recommendation_engaged", "selected_product_id", "price_interest"],
        allowed_actions=[SalesAction.ANSWER_CURRENT_QUESTION, SalesAction.RECOMMEND_SOLUTION],
        allowed_knowledge_sources=[
            *COMMON_KNOWLEDGE_SOURCES,
            SalesKnowledgeSource.PRODUCT_CATALOG,
        ],
        conditional_knowledge_sources=[SalesKnowledgeSource.SKU_FACTS],
        required_slot_groups=[
            ["need_track", "region", "budget"],
            ["need_track", "placement", "color_preference"],
            ["need_track", "pain_point"],
            ["need_track", "desired_outcome"],
            ["need_track", "failed_history"],
        ],
        prohibited_behaviors=[
            "缺少商品事实时推荐具体商品",
            "一次堆砌大量商品",
            "客户没有明确购兰需求时用兰花替代陪伴服务作为首要方案",
        ],
    ),
    SalesStageDefinition(
        stage=SalesStage.VALUE_BUILT,
        display_name="塑品",
        sequence=5,
        objective="优先建立客户对陪伴养兰、系统基础知识和持续指导价值的认知",
        entry_evidence_any=["recommendation_engaged", "selected_product_id"],
        exit_evidence_any=["value_acknowledged", "price_interest", "ready_to_buy"],
        allowed_actions=[SalesAction.ANSWER_CURRENT_QUESTION, SalesAction.BUILD_VALUE],
        allowed_knowledge_sources=[
            *COMMON_KNOWLEDGE_SOURCES,
            SalesKnowledgeSource.PRODUCT_CATALOG,
            SalesKnowledgeSource.PRODUCT_VALUE,
        ],
        conditional_knowledge_sources=[SalesKnowledgeSource.SKU_FACTS],
        required_slot_groups=[["selected_product_id"], ["selected_sku_id"]],
        prohibited_behaviors=["编造销量或苗质", "承诺无法核实的服务权益"],
    ),
    SalesStageDefinition(
        stage=SalesStage.TRIAL_CLOSE,
        display_name="试成交",
        sequence=6,
        objective="给出可信报价或方案，降低决策难度并试探下单意愿",
        entry_evidence_any=["price_interest", "value_acknowledged", "ready_to_buy"],
        exit_evidence_any=["ready_to_buy", "objection", "purchase_rejected"],
        allowed_actions=[SalesAction.ANSWER_CURRENT_QUESTION, SalesAction.TRIAL_CLOSE],
        allowed_knowledge_sources=[
            *COMMON_KNOWLEDGE_SOURCES,
            SalesKnowledgeSource.PRODUCT_CATALOG,
            SalesKnowledgeSource.PRODUCT_VALUE,
            SalesKnowledgeSource.SKU_FACTS,
        ],
        conditional_knowledge_sources=[
            SalesKnowledgeSource.PROMOTION,
            SalesKnowledgeSource.ORDER_FACTS,
        ],
        required_slot_groups=[["selected_sku_id", "quantity"], ["selected_product_id", "quantity"]],
        prohibited_behaviors=["没有可信价格事实就报价", "客户未确认方案时假设其已同意购买"],
    ),
    SalesStageDefinition(
        stage=SalesStage.CLOSING,
        display_name="成交推进",
        sequence=7,
        objective="解决最后顾虑并推进真实下单或付款",
        entry_evidence_any=["ready_to_buy", "objection", "decision_blocker"],
        exit_evidence_any=["purchased", "purchase_rejected"],
        allowed_actions=[
            SalesAction.ANSWER_CURRENT_QUESTION,
            SalesAction.RESOLVE_BLOCKER,
            SalesAction.CLOSE_ORDER,
        ],
        allowed_knowledge_sources=[
            *COMMON_KNOWLEDGE_SOURCES,
            SalesKnowledgeSource.PRODUCT_CATALOG,
            SalesKnowledgeSource.PRODUCT_VALUE,
            SalesKnowledgeSource.SKU_FACTS,
            SalesKnowledgeSource.PROMOTION,
            SalesKnowledgeSource.ORDER_FACTS,
        ],
        required_slot_groups=[["decision_blocker"], ["selected_sku_id", "quantity"]],
        prohibited_behaviors=["制造虚假稀缺", "重复施压", "没有可信订单事实就宣称已经成交"],
    ),
)

SALES_STAGE_BY_VALUE = {
    definition.stage.value: definition for definition in SALES_STAGE_DEFINITIONS
}
SALES_STAGE_VALUES = tuple(SALES_STAGE_BY_VALUE)

LEGACY_STAGE_ALIASES: dict[str, SalesStage] = {
    "greeting": SalesStage.RAPPORT,
    "pain_confirmed": SalesStage.PAIN_DISCOVERY,
    "price_discussed": SalesStage.TRIAL_CLOSE,
    "objection_handling": SalesStage.CLOSING,
    "order_intent": SalesStage.CLOSING,
}

LEGACY_INTERRUPTION_ALIASES: dict[str, SalesInterruptionType] = {
    "after_sale": SalesInterruptionType.AFTER_SALE,
    "human_pending": SalesInterruptionType.HUMAN_PENDING,
}

def get_sales_stage_definitions() -> tuple[SalesStageDefinition, ...]:
    return SALES_STAGE_DEFINITIONS


def get_sales_stage_definition(stage: str | SalesStage) -> SalesStageDefinition | None:
    value = stage.value if isinstance(stage, SalesStage) else str(stage).strip()
    return SALES_STAGE_BY_VALUE.get(value)


def normalize_sales_stage_reference(
    value: str | SalesStage | None,
    *,
    new_first_order_opportunity: bool = False,
) -> SalesStageNormalization:
    if isinstance(value, SalesStage):
        return SalesStageNormalization(original_value=value.value, stage=value)

    original_value = value.strip() if isinstance(value, str) else None
    normalized_value = original_value or ""
    if normalized_value.startswith("SalesStage."):
        normalized_value = normalized_value.split(".", 1)[1].strip().lower()
    if normalized_value.startswith("stage:"):
        normalized_value = normalized_value.split(":", 1)[1].strip()

    if normalized_value == "unknown":
        return SalesStageNormalization(
            original_value=original_value,
            stage=SalesStage.RAPPORT if new_first_order_opportunity else None,
            is_legacy=True,
        )

    if normalized_value in SALES_STAGE_BY_VALUE:
        return SalesStageNormalization(
            original_value=original_value,
            stage=SalesStage(normalized_value),
        )

    interruption = LEGACY_INTERRUPTION_ALIASES.get(normalized_value)
    if interruption is not None:
        return SalesStageNormalization(
            original_value=original_value,
            interruption_type=interruption,
            is_legacy=True,
        )

    stage = LEGACY_STAGE_ALIASES.get(normalized_value)
    signals = (
        (CustomerSignal.READY_TO_BUY,)
        if normalized_value == "order_intent"
        else ()
    )
    return SalesStageNormalization(
        original_value=original_value,
        stage=stage,
        signals=signals,
        is_legacy=stage is not None,
    )


def normalize_sales_stage_value(
    value: str | SalesStage | None,
    *,
    fallback: str = "unknown",
    new_first_order_opportunity: bool = False,
) -> str:
    normalized = normalize_sales_stage_reference(
        value,
        new_first_order_opportunity=new_first_order_opportunity,
    )
    return normalized.stage.value if normalized.stage is not None else fallback

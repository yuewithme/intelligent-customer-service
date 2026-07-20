from app.schemas.sales_flow import (
    CustomerSignal,
    SalesInterruptionType,
    SalesStage,
)
from app.services.sales_stage_catalog import (
    SALES_STAGE_DEFINITIONS,
    get_sales_stage_definition,
    normalize_sales_stage_reference,
    normalize_sales_stage_value,
)
from app.services.tag_catalog import SYSTEM_TAG_CATEGORIES, filter_runtime_labels, system_tag_token


def test_catalog_defines_exactly_seven_ordered_stages_with_valid_actions():
    assert [item.stage for item in SALES_STAGE_DEFINITIONS] == list(SalesStage)
    assert [item.sequence for item in SALES_STAGE_DEFINITIONS] == list(range(1, 8))
    assert len({item.sequence for item in SALES_STAGE_DEFINITIONS}) == 7

    for definition in SALES_STAGE_DEFINITIONS:
        assert definition.display_name.strip()
        assert definition.objective.strip()
        assert definition.allowed_actions
        assert isinstance(definition.allowed_actions, tuple)
        assert all(
            isinstance(group, tuple) for group in definition.required_slot_groups
        )
        assert definition.prohibited_behaviors
        assert get_sales_stage_definition(definition.stage) == definition


def test_legacy_stage_values_normalize_to_the_new_contract():
    expected = {
        "greeting": SalesStage.RAPPORT,
        "need_discovery": SalesStage.NEED_DISCOVERY,
        "pain_confirmed": SalesStage.PAIN_DISCOVERY,
        "solution_recommended": SalesStage.SOLUTION_RECOMMENDED,
        "price_discussed": SalesStage.TRIAL_CLOSE,
        "objection_handling": SalesStage.CLOSING,
        "order_intent": SalesStage.CLOSING,
    }

    for legacy_value, stage in expected.items():
        normalized = normalize_sales_stage_reference(legacy_value)
        assert normalized.stage == stage
        assert normalize_sales_stage_value(legacy_value) == stage.value

    order_intent = normalize_sales_stage_reference("order_intent")
    assert order_intent.signals == (CustomerSignal.READY_TO_BUY,)


def test_unknown_and_interruptions_are_not_main_sales_stages():
    assert normalize_sales_stage_reference("unknown").stage is None
    assert normalize_sales_stage_value("unknown") == "unknown"
    assert (
        normalize_sales_stage_reference(
            "unknown", new_first_order_opportunity=True
        ).stage
        == SalesStage.RAPPORT
    )

    after_sale = normalize_sales_stage_reference("after_sale")
    human_pending = normalize_sales_stage_reference("human_pending")
    assert after_sale.stage is None
    assert after_sale.interruption_type == SalesInterruptionType.AFTER_SALE
    assert human_pending.stage is None
    assert human_pending.interruption_type == SalesInterruptionType.HUMAN_PENDING


def test_historical_display_values_do_not_enter_backend_stage_normalization():
    historical_values = {
        "interest",
        "knowledge_consulting",
        "care_support",
        "first_order_nurture",
    }
    for value in historical_values:
        assert normalize_sales_stage_value(value) == "unknown"

    writable_values = {
        item.name for item in SYSTEM_TAG_CATEGORIES["sales_stage"].values
    }
    assert writable_values == {
        "stage:unknown",
        *(f"stage:{stage.value}" for stage in SalesStage),
    }
    assert not {f"stage:{value}" for value in historical_values} & writable_values


def test_legacy_runtime_labels_are_canonicalized_and_unknown_values_are_dropped():
    assert system_tag_token("sales_stage", "greeting") == "stage:rapport"
    assert system_tag_token("sales_stage", "stage:unknown") == "stage:unknown"
    assert filter_runtime_labels(
        ["stage:greeting", "stage:order_intent", "stage:not_a_stage"]
    ) == ["stage:rapport", "stage:closing"]

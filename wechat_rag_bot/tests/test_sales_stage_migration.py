from app.schemas.sales_flow import SalesStage
from app.services.sales_stage_migration_service import migrate_sales_stage_record


def test_order_intent_migrates_to_closing_without_winning():
    stage, opportunity, changed = migrate_sales_stage_record(
        "order_intent", {"status": "active", "current_stage": "order_intent"}
    )

    assert changed is True
    assert stage == SalesStage.CLOSING.value
    assert opportunity["status"] == "active"
    assert "ready_to_buy" in opportunity["signals"]


def test_legacy_interruption_preserves_resume_stage():
    stage, opportunity, changed = migrate_sales_stage_record(
        "after_sale", {"previous_stage": "price_discussed", "status": "active"}
    )

    assert changed is True
    assert stage == SalesStage.TRIAL_CLOSE.value
    assert opportunity["status"] == "paused"
    assert opportunity["interruption"]["type"] == "after_sale"
    assert opportunity["interruption"]["resume_stage"] == "trial_close"


def test_canonical_record_migration_is_idempotent():
    source = {"current_stage": "pain_discovery", "sales_stage": "pain_discovery", "status": "active"}
    stage, opportunity, changed = migrate_sales_stage_record("pain_discovery", source)

    assert stage == "pain_discovery"
    assert opportunity == source
    assert changed is False

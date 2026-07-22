import json
from datetime import datetime, timezone

from sqlalchemy import select

from app.infrastructure.database.models import UserProfileModel
from app.domains.sales.schemas.sales_flow import CustomerSignal, SalesStage
from app.domains.sales.services.sales_stage_catalog import normalize_sales_stage_reference
from app.domains.customers.services.user_profile_service import _get_session


def migrate_sales_stage_record(current_stage: str | None, opportunity: dict | None) -> tuple[str, dict, bool]:
    source = dict(opportunity) if isinstance(opportunity, dict) else {}
    result = dict(source)
    raw_stage = result.get("current_stage") or result.get("sales_stage") or current_stage
    normalized = normalize_sales_stage_reference(raw_stage)
    changed = False

    if normalized.interruption_type is not None:
        resume_raw = result.get("previous_stage") or result.get("resume_stage")
        resume = normalize_sales_stage_reference(resume_raw).stage or SalesStage.RAPPORT
        result["interruption"] = {
            "type": normalized.interruption_type.value,
            "reason": "legacy_stage_migration",
            "resume_stage": resume.value,
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        result["status"] = "paused"
        target = resume
        changed = True
    else:
        target = normalized.stage
        if target is None and result:
            target = SalesStage.RAPPORT

    canonical = target.value if target is not None else "unknown"
    if raw_stage == "order_intent":
        canonical = SalesStage.CLOSING.value
        signals = [str(value) for value in result.get("signals", [])]
        if CustomerSignal.READY_TO_BUY.value not in signals:
            signals.append(CustomerSignal.READY_TO_BUY.value)
        result["signals"] = signals
        result["status"] = "active"
        changed = True
    if result:
        for key in ("current_stage", "sales_stage"):
            if result.get(key) != canonical:
                result[key] = canonical
                changed = True
    if current_stage != canonical and canonical != "unknown":
        changed = True
    return canonical, result, changed


def migrate_user_sales_stages(*, limit: int | None = None) -> dict:
    stats = {"scanned": 0, "migrated": 0, "unchanged": 0, "by_source_stage": {}}
    with _get_session() as session:
        query = select(UserProfileModel).order_by(UserProfileModel.user_id)
        if limit is not None:
            query = query.limit(max(0, limit))
        rows = session.scalars(query).all()
        for profile in rows:
            stats["scanned"] += 1
            source_stage = profile.current_stage or "unknown"
            stats["by_source_stage"][source_stage] = stats["by_source_stage"].get(source_stage, 0) + 1
            try:
                opportunity = json.loads(profile.active_opportunity_json or "{}")
            except json.JSONDecodeError:
                opportunity = {}
            stage, migrated, changed = migrate_sales_stage_record(source_stage, opportunity)
            if changed:
                if stage != "unknown":
                    profile.current_stage = stage
                profile.active_opportunity_json = json.dumps(migrated, ensure_ascii=False)
                profile.updated_at = datetime.now(timezone.utc)
                stats["migrated"] += 1
            else:
                stats["unchanged"] += 1
        session.commit()
    return stats

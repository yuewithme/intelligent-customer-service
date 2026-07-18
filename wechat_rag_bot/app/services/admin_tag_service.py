from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.db.models import (
    Base,
    CustomerLevelProfileModel,
    CustomerLevelPromptBindingModel,
    PromptBlockModel,
    TagCategoryModel,
    TagDefinitionModel,
    TagPromptBindingModel,
    UserProfileModel,
)
from app.schemas.admin_tag import (
    TagCategoryCreateRequest,
    TagCategoryUpdateRequest,
    TagCreateRequest,
    TagPromptInput,
    TagUpdateRequest,
)
from app.schemas.common import AppError, ErrorCode
from app.services import business_tag_prompt_service, customer_level_service, state_service
from app.services.tag_catalog import get_tag_categories, invalidate_cache


_sessionmakers: dict[str, sessionmaker] = {}
_tables = [
    TagCategoryModel.__table__,
    TagDefinitionModel.__table__,
    PromptBlockModel.__table__,
    TagPromptBindingModel.__table__,
    CustomerLevelProfileModel.__table__,
    CustomerLevelPromptBindingModel.__table__,
    UserProfileModel.__table__,
]


def list_tag_categories() -> dict:
    _ensure_defaults()
    with _get_session() as session:
        categories = session.scalars(
            select(TagCategoryModel).order_by(
                TagCategoryModel.position.asc(), TagCategoryModel.id.asc()
            )
        ).all()
        tags = session.scalars(
            select(TagDefinitionModel).order_by(
                TagDefinitionModel.position.asc(), TagDefinitionModel.id.asc()
            )
        ).all()
        bindings = session.scalars(
            select(TagPromptBindingModel)
            .where(TagPromptBindingModel.enabled.is_(True))
            .order_by(TagPromptBindingModel.priority.asc(), TagPromptBindingModel.id.asc())
        ).all()
        block_ids = {binding.prompt_block_id for binding in bindings}
        blocks = {
            row.block_id: row
            for row in session.scalars(
                select(PromptBlockModel).where(PromptBlockModel.block_id.in_(block_ids))
            ).all()
        } if block_ids else {}
        shared_counts = {
            block_id: count
            for block_id, count in session.execute(
                select(
                    TagPromptBindingModel.prompt_block_id,
                    func.count(TagPromptBindingModel.id),
                )
                .where(TagPromptBindingModel.enabled.is_(True))
                .group_by(TagPromptBindingModel.prompt_block_id)
            ).all()
        }

    bindings_by_tag: dict[tuple[str, str], list[TagPromptBindingModel]] = {}
    for binding in bindings:
        bindings_by_tag.setdefault((binding.category_id, binding.tag_value), []).append(binding)
    tags_by_category: dict[str, list[dict]] = {}
    total_prompts = 0
    for tag in tags:
        prompt_items = []
        for binding in bindings_by_tag.get((tag.category_id, tag.value), []):
            block = blocks.get(binding.prompt_block_id)
            if not block:
                continue
            prompt_items.append(
                {
                    "block_id": block.block_id,
                    "title": block.title,
                    "content": block.content,
                    "shared_count": shared_counts.get(block.block_id, 1),
                }
            )
        total_prompts += len(prompt_items)
        tags_by_category.setdefault(tag.category_id, []).append(
            {"id": tag.id, "value": tag.value, "prompts": prompt_items}
        )

    items = [
        {
            "id": category.id,
            "name": category.name,
            "prompt_rule": category.prompt_rule,
            "ai_assignable": category.ai_assignable,
            "exclusive": category.exclusive,
            "tags": tags_by_category.get(category.id, []),
        }
        for category in categories
    ]
    return {
        "items": items,
        "total_categories": len(items),
        "total_tags": len(tags),
        "total_prompts": total_prompts,
    }


def create_tag_category(request: TagCategoryCreateRequest) -> dict:
    _ensure_defaults()
    with _get_session() as session:
        if session.get(TagCategoryModel, request.id):
            _conflict("分类标识已存在")
        max_position = session.scalar(select(func.max(TagCategoryModel.position))) or 0
        session.add(
            TagCategoryModel(
                id=request.id,
                name=request.name.strip(),
                prompt_rule=request.prompt_rule.strip(),
                ai_assignable=request.ai_assignable,
                exclusive=request.exclusive,
                position=max_position + 1,
            )
        )
        session.commit()
    invalidate_cache()
    return _category_detail(request.id)


def update_tag_category(category_id: str, request: TagCategoryUpdateRequest) -> dict:
    _ensure_defaults()
    with _get_session() as session:
        category = _require_category(session, category_id)
        category.name = request.name.strip()
        category.prompt_rule = request.prompt_rule.strip()
        category.ai_assignable = request.ai_assignable
        category.exclusive = request.exclusive
        session.commit()
    invalidate_cache()
    return _category_detail(category_id)


def delete_tag_category(category_id: str) -> dict:
    _ensure_defaults()
    with _get_session() as session:
        category = _require_category(session, category_id)
        values = session.scalars(
            select(TagDefinitionModel.value).where(
                TagDefinitionModel.category_id == category_id
            )
        ).all()
        block_ids = session.scalars(
            select(TagPromptBindingModel.prompt_block_id).where(
                TagPromptBindingModel.category_id == category_id
            )
        ).all()
        session.execute(
            delete(TagPromptBindingModel).where(
                TagPromptBindingModel.category_id == category_id
            )
        )
        session.execute(
            delete(TagDefinitionModel).where(
                TagDefinitionModel.category_id == category_id
            )
        )
        session.delete(category)
        _remove_customer_level_legacy_bindings(session, values)
        _clean_orphan_prompt_blocks(session, block_ids)
        _replace_profile_tags(session, set(values), None)
        session.commit()
    invalidate_cache()
    state_service.remove_customer_tags(set(values))
    return {"id": category_id, "deleted_tags": len(values)}


def create_tag(category_id: str, request: TagCreateRequest) -> dict:
    _ensure_defaults()
    value = request.value.strip()
    with _get_session() as session:
        _require_category(session, category_id)
        if _find_tag_by_value(session, value):
            _conflict("标签名称已存在；标签在后端需全局唯一")
        max_position = session.scalar(
            select(func.max(TagDefinitionModel.position)).where(
                TagDefinitionModel.category_id == category_id
            )
        ) or 0
        tag = TagDefinitionModel(
            category_id=category_id,
            value=value,
            position=max_position + 1,
        )
        session.add(tag)
        session.flush()
        _sync_prompts(session, category_id, value, request.prompts)
        session.commit()
        tag_id = tag.id
    invalidate_cache()
    return _tag_detail(tag_id)


def update_tag(tag_id: int, request: TagUpdateRequest) -> dict:
    _ensure_defaults()
    new_value = request.value.strip()
    with _get_session() as session:
        tag = _require_tag(session, tag_id)
        old_value = tag.value
        duplicate = _find_tag_by_value(session, new_value)
        if duplicate and duplicate.id != tag.id:
            _conflict("标签名称已存在；标签在后端需全局唯一")
        if new_value != old_value:
            session.execute(
                TagPromptBindingModel.__table__.update()
                .where(
                    TagPromptBindingModel.category_id == tag.category_id,
                    TagPromptBindingModel.tag_value == old_value,
                )
                .values(tag_value=new_value)
            )
            tag.value = new_value
            _rename_customer_level_profile(session, old_value, new_value)
            _replace_profile_tags(session, {old_value}, new_value)
        _sync_prompts(session, tag.category_id, new_value, request.prompts)
        session.commit()
    invalidate_cache()
    if new_value != old_value:
        state_service.replace_customer_tag(old_value, new_value)
    return _tag_detail(tag_id)


def delete_tag(tag_id: int) -> dict:
    _ensure_defaults()
    with _get_session() as session:
        tag = _require_tag(session, tag_id)
        value = tag.value
        category_id = tag.category_id
        block_ids = session.scalars(
            select(TagPromptBindingModel.prompt_block_id).where(
                TagPromptBindingModel.category_id == category_id,
                TagPromptBindingModel.tag_value == value,
            )
        ).all()
        session.execute(
            delete(TagPromptBindingModel).where(
                TagPromptBindingModel.category_id == category_id,
                TagPromptBindingModel.tag_value == value,
            )
        )
        session.delete(tag)
        _remove_customer_level_legacy_bindings(session, [value])
        _clean_orphan_prompt_blocks(session, block_ids)
        _replace_profile_tags(session, {value}, None)
        session.commit()
    invalidate_cache()
    state_service.remove_customer_tags({value})
    return {"id": tag_id, "category_id": category_id, "value": value}


def _sync_prompts(
    session: Session,
    category_id: str,
    tag_value: str,
    prompts: list[TagPromptInput],
) -> None:
    current = session.scalars(
        select(TagPromptBindingModel).where(
            TagPromptBindingModel.category_id == category_id,
            TagPromptBindingModel.tag_value == tag_value,
        )
    ).all()
    current_by_id = {binding.prompt_block_id: binding for binding in current}
    keep_ids: set[str] = set()
    for priority, prompt in enumerate(prompts, start=1):
        block_id = prompt.block_id
        if block_id:
            binding = current_by_id.get(block_id)
            if binding is None:
                raise AppError(ErrorCode.REQUEST_INVALID, "提示词不属于当前标签")
            block = session.get(PromptBlockModel, block_id)
            if block is None:
                raise AppError(ErrorCode.REQUEST_INVALID, "提示词不存在")
            block.title = prompt.title.strip()
            block.content = prompt.content.strip()
            block.enabled = True
            binding.priority = priority
            binding.enabled = True
        else:
            block_id = f"tag.{category_id}.{uuid4().hex}"
            session.add(
                PromptBlockModel(
                    block_id=block_id,
                    title=prompt.title.strip(),
                    content=prompt.content.strip(),
                    enabled=True,
                )
            )
            session.add(
                TagPromptBindingModel(
                    category_id=category_id,
                    tag_value=tag_value,
                    prompt_block_id=block_id,
                    priority=priority,
                    enabled=True,
                )
            )
        keep_ids.add(block_id)

    removed_ids = [block_id for block_id in current_by_id if block_id not in keep_ids]
    if removed_ids:
        session.execute(
            delete(TagPromptBindingModel).where(
                TagPromptBindingModel.category_id == category_id,
                TagPromptBindingModel.tag_value == tag_value,
                TagPromptBindingModel.prompt_block_id.in_(removed_ids),
            )
        )
        if category_id == "customer_level":
            level = _customer_level_from_value(tag_value)
            if level:
                session.execute(
                    delete(CustomerLevelPromptBindingModel).where(
                        CustomerLevelPromptBindingModel.level == level,
                        CustomerLevelPromptBindingModel.prompt_block_id.in_(removed_ids),
                    )
                )
        session.flush()
        _clean_orphan_prompt_blocks(session, removed_ids)


def _replace_profile_tags(
    session: Session,
    old_values: set[str],
    replacement: str | None,
) -> None:
    if not old_values:
        return
    for profile in session.scalars(select(UserProfileModel)).all():
        try:
            tags = json.loads(profile.customer_tags_json or "[]")
        except (TypeError, ValueError):
            tags = []
        if not isinstance(tags, list) or not any(tag in old_values for tag in tags):
            continue
        updated = []
        for tag in tags:
            value = replacement if tag in old_values else tag
            if value and value not in updated:
                updated.append(value)
        profile.customer_tags_json = json.dumps(updated, ensure_ascii=False)
        profile.updated_at = datetime.now(timezone.utc)


def _clean_orphan_prompt_blocks(session: Session, block_ids: list[str]) -> None:
    for block_id in set(block_ids):
        has_tag_binding = session.scalar(
            select(TagPromptBindingModel.id)
            .where(TagPromptBindingModel.prompt_block_id == block_id)
            .limit(1)
        )
        has_level_binding = session.scalar(
            select(CustomerLevelPromptBindingModel.id)
            .where(CustomerLevelPromptBindingModel.prompt_block_id == block_id)
            .limit(1)
        )
        if has_tag_binding is None and has_level_binding is None:
            block = session.get(PromptBlockModel, block_id)
            if block:
                session.delete(block)


def _remove_customer_level_legacy_bindings(session: Session, values: list[str]) -> None:
    levels = {_customer_level_from_value(value) for value in values}
    levels.discard(None)
    if levels:
        session.execute(
            delete(CustomerLevelPromptBindingModel).where(
                CustomerLevelPromptBindingModel.level.in_(levels)
            )
        )


def _rename_customer_level_profile(
    session: Session, old_value: str, new_value: str
) -> None:
    profile = session.scalar(
        select(CustomerLevelProfileModel).where(CustomerLevelProfileModel.name == old_value)
    )
    if profile:
        profile.name = new_value


def _customer_level_from_value(value: str) -> str | None:
    prefix = value.split(" ", 1)[0]
    return prefix if prefix in {"L1", "L2", "L3", "L4", "L5", "L6"} else None


def _category_detail(category_id: str) -> dict:
    return next(
        item for item in list_tag_categories()["items"] if item["id"] == category_id
    )


def _tag_detail(tag_id: int) -> dict:
    for category in list_tag_categories()["items"]:
        for tag in category["tags"]:
            if tag["id"] == tag_id:
                return {**tag, "category_id": category["id"]}
    raise AppError(ErrorCode.REQUEST_INVALID, "标签不存在", status_code=404)


def _require_category(session: Session, category_id: str) -> TagCategoryModel:
    category = session.get(TagCategoryModel, category_id)
    if category is None:
        raise AppError(ErrorCode.REQUEST_INVALID, "标签分类不存在", status_code=404)
    return category


def _require_tag(session: Session, tag_id: int) -> TagDefinitionModel:
    tag = session.get(TagDefinitionModel, tag_id)
    if tag is None:
        raise AppError(ErrorCode.REQUEST_INVALID, "标签不存在", status_code=404)
    return tag


def _find_tag_by_value(session: Session, value: str) -> TagDefinitionModel | None:
    return session.scalar(
        select(TagDefinitionModel).where(TagDefinitionModel.value == value)
    )


def _conflict(message: str) -> None:
    raise AppError(ErrorCode.REQUEST_INVALID, message, status_code=409)


def _ensure_defaults() -> None:
    get_tag_categories()
    customer_level_service.get_customer_level_prompt_block_ids("L1")
    business_tag_prompt_service.ensure_business_tag_prompt_policy()


def clear_cache() -> None:
    _sessionmakers.clear()


def _get_session() -> Session:
    url = get_settings().database_url
    factory = _sessionmakers.get(url)
    if factory is None:
        engine = create_engine(url)
        Base.metadata.create_all(engine, tables=_tables)
        factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        _sessionmakers[url] = factory
    return factory()

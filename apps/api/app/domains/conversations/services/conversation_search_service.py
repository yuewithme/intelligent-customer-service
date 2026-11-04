import re
from datetime import timezone

from sqlalchemy import and_, case, func, inspect, or_, select

from app.core.config import get_settings
from app.infrastructure.database.models import (
    ConversationMessageModel,
    ConversationModel,
    EyunContactModel,
    UserProfileModel,
)


IDENTITY_FIELDS = {
    "wechat_id": (100, "微信号", ("alias_name", "wechat_id", "wechatId", "alias")),
    "remark": (90, "备注", ("remark_name", "remark")),
    "nickname": (80, "昵称", ("nickname", "nick_name", "user_nickname")),
    "display_name": (60, "显示名", ("display_name", "sender_name")),
}
MESSAGE_WEIGHT = 30
IDENTITY_WHITESPACE = (
    "\t\n\v\f\r\x1c\x1d\x1e\x1f \x85\xa0\u1680\u2000\u2001\u2002\u2003\u2004\u2005"
    "\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000"
)


def _identity_values(data):
    return {
        field: next(
            (str(data[key]).strip() for key in keys if isinstance(data.get(key), str) and data[key].strip()),
            "",
        )
        for field, (_, _, keys) in IDENTITY_FIELDS.items()
    }


def _stored_identities(conversations, chat_session):
    from app.domains.customers.services.user_profile_service import _get_session
    from app.domains.conversations.services.conversation_service import _load_metadata

    identities = {
        row.conversation_id: {"display_name": row.user_display_name or ""}
        for row in conversations
    }
    wechat_rows = [row for row in conversations if row.channel == "wechat"]
    if not wechat_rows:
        return identities
    by_user = {}
    for row in wechat_rows:
        by_user.setdefault(row.user_id, []).append(row)
    user_ids = list(by_user)
    configured_owner = get_settings().eyun_wc_id.strip()
    with _get_session() as session:
        has_contacts = inspect(session.get_bind()).has_table(EyunContactModel.__tablename__)
        for offset in range(0, len(user_ids), 200):
            batch = user_ids[offset : offset + 200]
            owner_scopes = {}
            for user_id, tenant_id, owner_id in chat_session.execute(
                select(ConversationModel.user_id, ConversationModel.tenant_id, ConversationModel.owner_wc_id)
                .where(ConversationModel.channel == "wechat", ConversationModel.user_id.in_(batch))
                .distinct()
            ):
                owner_scopes.setdefault(user_id, set()).add((tenant_id, owner_id))
            profiles = session.scalars(select(UserProfileModel).where(UserProfileModel.user_id.in_(batch)))
            for profile in profiles:
                basic = _load_metadata(profile.basic_info_json)
                owner = str(basic.get("owner_wc_id") or "").strip()
                for row in by_user[profile.user_id]:
                    # Profiles are keyed by user globally; only use a verified account scope.
                    if owner:
                        scoped = owner == row.owner_wc_id
                    else:
                        scoped = len(owner_scopes.get(profile.user_id, ())) == 1 and (
                            profile.tenant_id == row.tenant_id
                            or (
                                profile.tenant_id == "tenant_default"
                                and bool(configured_owner)
                                and row.owner_wc_id == configured_owner
                            )
                        )
                    if not scoped or profile.channel not in {row.channel, "api"}:
                        continue
                    for field, value in _identity_values(basic).items():
                        if value:
                            identities[row.conversation_id][field] = value
            if not has_contacts:
                continue
            contacts = session.scalars(select(EyunContactModel).where(EyunContactModel.wc_id.in_(batch)))
            for contact in contacts:
                for row in by_user[contact.wc_id]:
                    same_account = (
                        contact.tenant_id == row.tenant_id
                        and contact.owner_account_id == (row.owner_wc_id or row.tenant_id)
                    )
                    legacy_default = (
                        contact.tenant_id == "tenant_default"
                        and contact.owner_account_id == "default"
                        and bool(configured_owner)
                        and row.owner_wc_id == configured_owner
                    )
                    if not (same_account or legacy_default):
                        continue
                    for field, value in (
                        ("remark", contact.remark_name),
                        ("wechat_id", contact.wechat_id),
                        ("display_name", contact.display_name),
                    ):
                        if value and not identities[row.conversation_id].get(field):
                            identities[row.conversation_id][field] = value
    return identities


def _match_score(value, term, weight):
    value = value.casefold()
    if term not in value:
        return 0
    return weight * (1.5 if value == term else 1.2 if value.startswith(term) else 1)


def _snippet(value, terms):
    value = re.sub(r"\s+", " ", value).strip()
    positions = [value.casefold().find(term) for term in terms if term in value.casefold()]
    start = max(0, min(positions, default=0) - 24)
    return ("…" if start else "") + value[start : start + 120] + ("…" if start + 120 < len(value) else "")


def _snapshot_identities(session, scope, identities):
    missing = {field: [cid for cid, values in identities.items() if not values.get(field)] for field in IDENTITY_FIELDS}
    missing = {field: ids for field, ids in missing.items() if ids}
    if not missing:
        return
    message = ConversationMessageModel
    metadata = case((func.json_valid(message.metadata_json), message.metadata_json), else_="{}")
    revoked = func.json_extract(metadata, "$.revoked_at")
    revoked_type = func.json_type(metadata, "$.revoked_at")
    not_revoked = or_(
        revoked.is_(None), revoked == 0,
        and_(revoked_type == "text", revoked == ""),
        and_(revoked_type.in_(("array", "object")), revoked.in_(("[]", "{}"))),
    )
    columns = []
    for field, ids in missing.items():
        values = [
            func.nullif(case((
                func.json_type(metadata, f"$.{key}") == "text",
                func.trim(func.json_extract(metadata, f"$.{key}"), IDENTITY_WHITESPACE),
            )), "")
            for key in IDENTITY_FIELDS[field][2]
        ]
        value = func.coalesce(*values, "")
        # Each field can come from a different message; stop at its latest valid value.
        latest = (
            select(value)
            .where(
                message.conversation_id == scope.c.conversation_id,
                message.sender_type == "customer",
                or_(message.delivery_status.is_(None), message.delivery_status != "revoked"),
                not_revoked, value != "",
            )
            .order_by(message.created_at.desc(), message.id.desc())
            .limit(1)
            .correlate(scope)
            .scalar_subquery()
        )
        columns.append(case((scope.c.conversation_id.in_(ids), latest)).label(field))
    snapshots = session.execute(
        select(scope.c.conversation_id, *columns)
        .where(scope.c.conversation_id.in_({cid for ids in missing.values() for cid in ids}))
    )
    for snapshot in snapshots:
        for field in missing:
            value = getattr(snapshot, field)
            if value:
                identities[snapshot.conversation_id][field] = value


def search_conversations(session, filters, keyword):
    from app.domains.conversations.services.conversation_service import _load_metadata
    from app.domains.conversations.services.media_context_service import message_context_text

    terms = list(dict.fromkeys(keyword.casefold().split()))
    rows = session.scalars(select(ConversationModel).where(*filters)).all()
    if not rows:
        return []
    identities = _stored_identities(rows, session)
    scope = select(ConversationModel.conversation_id).where(*filters).subquery()

    _snapshot_identities(session, scope, identities)

    scores = {row.conversation_id: {} for row in rows}
    matches = {row.conversation_id: {} for row in rows}

    def consider(conversation_id, value, weight, field, label, message=None):
        if not value:
            return
        term_scores = scores[conversation_id]
        match_scores = []
        for term in terms:
            score = _match_score(value, term, weight)
            if score:
                term_scores[term] = max(term_scores.get(term, 0), score)
                match_scores.append(score)
        if not match_scores:
            return
        strength = (len(match_scores), sum(match_scores))
        previous = matches[conversation_id].get(field)
        if previous and previous[0] >= strength:
            return
        match = {"field": field, "label": label, "text": _snippet(value, terms)}
        if message is not None:
            created_at = message.created_at
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            match.update(message_id=message.id, created_at=created_at.isoformat())
        matches[conversation_id][field] = (strength, match)

    for row in rows:
        values = identities[row.conversation_id]
        if row.customer_remark:
            consider(row.conversation_id, row.customer_remark, 90, "remark", "备注")
        # The stable wxid remains searchable even when a public WeChat alias is available.
        consider(row.conversation_id, row.user_id, 100, "user_id", "微信标识")
        for field, (weight, label, _) in IDENTITY_FIELDS.items():
            consider(row.conversation_id, values.get(field), weight, field, label)

    has_media = or_(*(ConversationMessageModel.metadata_json.contains(f'"{key}"')
        for key in ("media", "link_card", "mini_program")))
    # Plain text does not need provider payloads, receipts or AI traces to be searched or returned.
    search_metadata = case((has_media, ConversationMessageModel.metadata_json), else_="{}")
    visible_metadata = case((or_(has_media, ConversationMessageModel.metadata_json.contains('"revoked_at"')),
        ConversationMessageModel.metadata_json), else_="{}").label("metadata_json")
    messages = session.execute(
        select(ConversationMessageModel.id, ConversationMessageModel.conversation_id,
            ConversationMessageModel.created_at, ConversationMessageModel.content,
            ConversationMessageModel.delivery_status, visible_metadata)
        .join(scope, scope.c.conversation_id == ConversationMessageModel.conversation_id)
        .where(
            ConversationMessageModel.sender_type.in_(("customer", "ai", "human")),
            or_(*(
                column.icontains(term, autoescape=True)
                for term in terms
                for column in (ConversationMessageModel.content, search_metadata)
            )),
        )
        .order_by(ConversationMessageModel.created_at.desc(), ConversationMessageModel.id.desc())
    ).yield_per(200)
    for message in messages:
        metadata = _load_metadata(message.metadata_json)
        if message.delivery_status in {"revoked", "cancelled"} or metadata.get("revoked_at"):
            continue
        content = message_context_text({"content": message.content, "metadata": metadata})
        consider(message.conversation_id, content, MESSAGE_WEIGHT, "message", "聊天记录", message)

    ranked = []
    for row in rows:
        term_scores = scores[row.conversation_id]
        if not term_scores:
            continue
        # Coverage dominates field weights; repeated messages cannot inflate relevance.
        score = 1000 * len(term_scores) + sum(term_scores.values()) / len(terms)
        details = sorted(matches[row.conversation_id].values(), key=lambda match: match[0], reverse=True)
        ranked.append((row, {"search_score": round(score, 2), "search_matches": [match[1] for match in details]}))
    ranked.sort(key=lambda item: (item[1]["search_score"], item[0].updated_at, item[0].id), reverse=True)
    return ranked

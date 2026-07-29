import json
import re

from app.domains.decisioning.schemas.reply import FinalReply
from app.domains.decisioning.schemas.reply_plan import BusinessFacts
from app.integrations.ai.services.llm_service import generate_answer


INTERNAL_KEYS = {
    "payment_status",
    "order_lookup",
    "activity_status",
    "shipping_date_change_executed",
    "course_status",
}
UNANSWERABLE_FACT_KEYS = {"order_lookup", "activity_status", "course_status"}
UNANSWERABLE_FACT_VALUES = {"unavailable", "unknown", "unverified", "not_found"}


def build_business_prompt(*, question: str, facts: BusinessFacts) -> str:
    payload = json.dumps(facts.model_dump(), ensure_ascii=False, sort_keys=True)
    return f"""你是兰花客服。只根据给定事实回答当前问题。
规则：
1. 不得增加价格、库存、权益、执行结果或时间承诺。
2. false、unknown、unverified、not_found、failed 表示事项未完成或未确认。
3. 不得向客户输出英文内部字段名。
4. 已付款、已执行和已开通必须有明确的肯定事实才能确认。
5. 用简洁自然的中文说明现状和下一步。
6. 如果给定事实不足以可靠回答，只输出 __HANDOFF__。

【业务事实】
{payload}

【客户当前问题】
{question.strip()}
"""


def _contains_internal_key(answer: str, facts: BusinessFacts) -> bool:
    keys = INTERNAL_KEYS | set(facts.tool_state)
    return any(key in answer for key in keys)


async def render_business_reply(
    message,
    facts: BusinessFacts | None = None,
) -> FinalReply | None:
    metadata = message.metadata if isinstance(message.metadata, dict) else {}
    tool_state = metadata.get("tool_state")
    facts = facts or BusinessFacts(
        snapshot=str(metadata.get("business_snapshot") or "").strip(),
        tool_state=dict(tool_state) if isinstance(tool_state, dict) else {},
    )
    if (
        metadata.get("demo")
        and facts.tool_state.get("commerce_type") == "product"
        and facts.tool_state.get("status") == "not_found"
    ):
        return None
    commerce_reply = _render_commerce_reply(facts)
    if commerce_reply is not None:
        return commerce_reply
    if facts.tool_state.get("commerce_type") in {"product", "order"}:
        return None
    if any(
        facts.tool_state.get(key) in UNANSWERABLE_FACT_VALUES
        for key in UNANSWERABLE_FACT_KEYS
    ):
        return None
    result = await generate_answer(
        build_business_prompt(question=message.message, facts=facts),
        purpose="business",
    )
    answer = str(result.get("answer") or "").strip()
    if not answer or answer == "__HANDOFF__" or _contains_internal_key(answer, facts):
        return None
    return FinalReply(
        answer=answer,
        reply_type="template",
        route="template_reply",
        usage=dict(result.get("usage") or {}),
        metadata={"business_facts_used": True},
    )


def _render_commerce_reply(facts: BusinessFacts) -> FinalReply | None:
    state = facts.tool_state
    commerce_type = state.get("commerce_type")
    if commerce_type not in {"product", "order"}:
        return None

    status = state.get("status")
    if status == "unavailable":
        return None
    if status == "missing_product":
        return _commerce_final_reply("可以的，您想看哪一个品种或规格？", state)
    if commerce_type == "product":
        products = state.get("products") if isinstance(state.get("products"), list) else []
        if status == "not_found" and state.get("query_performed"):
            return _commerce_final_reply(
                "我按您当前的条件查了在售商品，暂时没有匹配项。"
                "您可以放宽一个条件，我再继续帮您筛选。",
                state,
            )
        if status != "found" or not products:
            return None
        else:
            first = products[0]
            if state.get("product_request_kind") == "selected_product_detail":
                return _render_selected_product_detail(first, state)
            if state.get("product_request_kind") == "membership":
                price = first.get("price_cent") if isinstance(first, dict) else None
                price_text = (
                    f"，当前售价{price / 100:g}元" if isinstance(price, int) else ""
                )
                answer = (
                    f"可以，店里的会员资格可以直接购买{price_text}。"
                    "下面是对应的真实商品卡片，点击就可以查看和下单。"
                )
                return _commerce_final_reply(answer, state)
            if state.get("product_request_kind") == "supply_shortage":
                keywords = state.get("requested_product_keywords")
                keywords = keywords if isinstance(keywords, list) else []
                labels = []
                if any("盆" in str(keyword) for keyword in keywords):
                    labels.append("花盆")
                if any("植料" in str(keyword) for keyword in keywords):
                    labels.append("植料")
                subject = "和".join(labels) or "养兰用品"
                answer = (
                    f"可以，{subject}能单独补。下面是对应的真实商品卡片，"
                    "您可以先看规格；如果需要估算数量，告诉我准备上盆多少株。"
                )
                return _commerce_final_reply(answer, state)
            if state.get("send_product_image"):
                if first.get("image_url"):
                    answer = (
                        f"可以的，这是{_product_display_name(first)}的商品图片，"
                        "您可以看看花色和株型。"
                    )
                else:
                    answer = "暂时没有同步到这款商品的图片，您可以先查看商品卡片。"
                return _commerce_final_reply(answer, state)
            price = first.get("price_cent") if isinstance(first, dict) else None
            price_text = f"，当前售价{price / 100:g}元" if isinstance(price, int) else ""
            card = state.get("mini_program")
            if isinstance(card, dict) and card.get("app_id") and card.get("page_path"):
                next_step = "点击商品卡片就可以查看和下单。"
            elif first.get("h5_url"):
                next_step = "点击下方商品卡片就可以查看详情和下单。"
            else:
                next_step = "如果需要下单，我再帮您确认购买入口。"
            knowledge_text = _product_knowledge_text(first)
            capability_note = _requested_capability_note(state)
            answer = (
                f"推荐您看看{_product_display_name(first)}{price_text}"
                f"{knowledge_text}。{capability_note}{next_step}"
            )
        return _commerce_final_reply(answer, state)

    requested_action = str(state.get("requested_action") or "")
    if status == "missing_mobile":
        if requested_action == "shipping_date_change":
            answer = (
                "可以，我先核对订单再处理发货时间。"
                "请把下单手机号发给我，我查到订单后再确认能否调整。"
            )
        elif requested_action == "verify_material_entitlement":
            answer = (
                "我先帮您核对购买记录和对应资料权限。"
                "请把下单手机号发给我，我查到订单后再给您准确答复。"
            )
        else:
            answer = "可以的，请把下单手机号发给我，我帮您查询一下。"
        return _commerce_final_reply(answer, state)
    orders = state.get("orders") if isinstance(state.get("orders"), list) else []
    if status == "not_found" and state.get("lookup_performed"):
        return _commerce_final_reply(
            "我刚按当前微信身份或已留手机号查询了，暂时没有匹配到订单。"
            "请发一下订单号或订单截图，我再按订单信息继续核对。",
            state,
        )
    if status != "found" or not orders:
        return None

    lines = ["查到您近期的订单："]
    for index, order in enumerate(orders, start=1):
        if not isinstance(order, dict):
            continue
        detail = f"{index}. {order.get('item_summary') or '商品'}，{order.get('status_text') or '状态待确认'}"
        if order.get("express_company"):
            detail += f"，{order['express_company']}"
        if order.get("tracking_no_masked"):
            detail += f"，单号{order['tracking_no_masked']}"
        lines.append(detail)
    card = state.get("mini_program")
    if isinstance(card, dict) and card.get("app_id") and card.get("page_path"):
        lines.append("详细信息可以点击订单卡片查看。")
    else:
        lines.append("以上为有赞最新查询结果。")
    if requested_action == "shipping_date_change":
        lines.append(
            "您提出的发货时间调整目前还没有执行；我这里只确认已经查到订单，"
            "不会先答应已经改好。"
        )
    elif requested_action == "verify_material_entitlement":
        lines.append(
            "购买记录已经核对；教程或资料权限还需要按对应资料领取记录继续核实，"
            "我不会先承诺已经开通。"
        )
    return _commerce_final_reply("\n".join(lines), state)


def _render_selected_product_detail(product: dict, state: dict) -> FinalReply:
    name = _product_display_name(product)
    skus = product.get("skus")
    skus = skus if isinstance(skus, list) else []
    specs = []
    for sku in skus:
        if not isinstance(sku, dict):
            continue
        spec_name = str(sku.get("spec_name") or "").strip()
        if not spec_name or spec_name in specs:
            continue
        price = sku.get("price_cent")
        price_text = f"（{price / 100:g}元）" if isinstance(price, int) else ""
        specs.append(f"{spec_name}{price_text}")
    question = str(state.get("detail_question") or "")
    if specs:
        answer = f"{name}当前同步到的规格有：{'、'.join(specs[:6])}。"
        if "带盆" in question and not any(
            marker in spec for spec in specs for marker in ("带盆", "含盆", "盆栽", "种好")
        ):
            answer += "现有规格没有明确标注带盆或种好发货，我不能先按带盆款承诺。"
    else:
        answer = (
            f"{name}当前商品资料没有同步出可确认的苗数或带盆规格，"
            "我不能凭商品名称猜测；请以商品卡片规格页为准。"
        )
    return _commerce_final_reply(answer, state)


def _product_knowledge_text(product: dict) -> str:
    knowledge = product.get("knowledge")
    if not isinstance(knowledge, dict):
        return ""
    sales_copy = re.sub(r"\s+", "", str(knowledge.get("sales_copy") or "")).strip()
    sales_copy = sales_copy.rstrip("。！？!?；; ")
    if sales_copy:
        return f"。{_without_special_symbols(sales_copy)}"
    features = str(knowledge.get("highlighted_features") or "").strip()
    if features:
        feature_items = [
            re.sub(r"^\s*\d+[.、]\s*[^：:]{0,12}[：:]\s*", "", item).strip(" 。；;")
            for item in re.split(r"[\r\n]+|(?=\s*\d+[.、])", features)
        ]
        concise = [item for item in feature_items if item][:2]
        if concise:
            return f"，{'；'.join(item[:70] for item in concise)}"
    details = []
    for label, key in (
        ("花色", "flower_color"),
        ("香味", "fragrance"),
        ("花期", "bloom_period"),
        ("适合", "care_scenes"),
    ):
        value = str(knowledge.get(key) or "").strip()
        if value:
            details.append(f"{label}{value}")
        if len(details) == 3:
            break
    return f"，{'，'.join(details)}" if details else ""


def _requested_capability_note(state: dict) -> str:
    capabilities = state.get("requested_capabilities")
    capabilities = capabilities if isinstance(capabilities, list) else []
    if "video_tutorial" in capabilities:
        return (
            "当前商品信息没有单独标明视频教学权益，"
            "这一项仍需按购买记录核实，我不先承诺。"
        )
    return ""


def _product_display_name(product: dict) -> str:
    knowledge = product.get("knowledge")
    if isinstance(knowledge, dict):
        name = str(knowledge.get("product_name") or "").strip()
        category = str(knowledge.get("category") or "").strip()
        if name:
            name = re.sub(r"[（(].*?[）)]", "", name).strip()
            return f"{category}{name}" if category else name
    return _without_special_symbols(str(product.get("title") or "这款商品").strip())


def _without_special_symbols(text: str) -> str:
    return str(text or "").translate(
        str.maketrans("", "", "“”‘’\"'「」『』【】[]（）()—–")
    )


def _commerce_final_reply(answer: str, state: dict) -> FinalReply:
    outbound_messages = [{"type": "text", "content": answer, "split": False}]
    if state.get("send_product_image") and state.get("commerce_type") == "product":
        products = state.get("products") if isinstance(state.get("products"), list) else []
        first = products[0] if products and isinstance(products[0], dict) else {}
        image_urls = first.get("image_urls")
        if not isinstance(image_urls, list):
            image_urls = []
        image_urls = [first.get("image_url"), *image_urls]
        unique_image_urls = []
        for image_url in image_urls:
            value = str(image_url or "").strip()
            if value and value not in unique_image_urls:
                unique_image_urls.append(value)
        for image_url in unique_image_urls[:3]:
            outbound_messages.append(
                {"type": "image", "content": image_url}
            )
    card = state.get("mini_program")
    if isinstance(card, dict) and card.get("app_id") and card.get("page_path"):
        products = state.get("products") if isinstance(state.get("products"), list) else []
        card_products = (
            products[:3] if state.get("send_all_product_cards") else products[:1]
        )
        cards = []
        for product in card_products:
            if not isinstance(product, dict) or not product.get("page_path"):
                continue
            cards.append(
                {
                    **card,
                    "page_path": product["page_path"],
                    "thumb_url": product.get("image_url") or "",
                    "title": product.get("title") or card.get("title") or "",
                }
            )
        for product_card in cards or [card]:
            outbound_messages.append(
                {
                    "type": "mini_program",
                    "content": json.dumps(product_card, ensure_ascii=False),
                }
            )
    elif state.get("commerce_type") == "product":
        products = state.get("products") if isinstance(state.get("products"), list) else []
        card_products = (
            products[:3] if state.get("send_all_product_cards") else products[:1]
        )
        for product in card_products:
            if not isinstance(product, dict) or not product.get("h5_url"):
                continue
            price = product.get("price_cent")
            description = (
                f"当前售价{price / 100:g}元，点击查看详情和下单"
                if isinstance(price, int)
                else "点击查看商品详情和下单"
            )
            outbound_messages.append(
                {
                    "type": "link_card",
                    "content": json.dumps(
                        {
                            "title": (
                                _product_display_name(product)
                                .replace("“", "")
                                .replace("”", "")
                            ),
                            "url": product["h5_url"],
                            "description": description,
                            "thumb_url": product.get("image_url") or "",
                        },
                        ensure_ascii=False,
                    ),
                }
            )
    commerce_action = {
        "commerce_type": state.get("commerce_type"),
        "status": state.get("status"),
        "requested_action": state.get("requested_action"),
        "requested_action_executed": state.get("requested_action_executed"),
        "card_sent": any(
            message["type"] in {"mini_program", "link_card"}
            for message in outbound_messages
        ),
    }
    if state.get("fixture_used"):
        commerce_action["fixture_used"] = True
    return FinalReply(
        answer=answer,
        outbound_messages=outbound_messages,
        reply_type="template",
        route="template_reply",
        metadata={
            "business_facts_used": True,
            "allow_persona_extension": (
                (
                    state.get("commerce_type") == "product"
                    and state.get("status") == "found"
                )
                or (
                    state.get("commerce_type") == "order"
                    and state.get("status") in {"missing_mobile", "found", "not_found"}
                )
            ),
            "commerce_action": commerce_action,
        },
    )

from app.domains.orchestration.schemas.canvas_workflow import CanvasDefinition
from app.domains.orchestration.schemas.sop_flow import FlowSchedule, SopFlowDefinition


def _schedule_text(schedule: FlowSchedule) -> str:
    period = "每天" if schedule.weekday is None else "每周" + "一二三四五六日"[schedule.weekday]
    return f"北京时间{period} {schedule.time}"


def _entry_text(flow: SopFlowDefinition, category_names: dict[str, str]) -> str:
    rule = flow.entry_rule
    conditions = []
    if rule.any_tags:
        conditions.append("至少带有以下一个标签：" + "、".join(rule.any_tags))
    if rule.required_tags:
        conditions.append("同时带有以下全部标签：" + "、".join(rule.required_tags))
    if rule.excluded_tags:
        conditions.append("不带有以下任一标签：" + "、".join(rule.excluded_tags))
    if rule.tag_categories:
        conditions.append("以下每个标签类别均至少已有一个标签：" + "、".join(
            category_names.get(key, key) for key in rule.tag_categories))
    if rule.fallback_only:
        conditions.append("仅在没有命中其他已开启的 SOP 时进入")
    text = "流程启用后，客户加上好友、收到客户消息或客户标签变化时，检查入口条件。"
    text += "\n入口条件（以下各项须同时满足）：\n" + "\n".join(conditions) if conditions else "\n不额外限制客户标签。"
    schedules = list(dict.fromkeys(_schedule_text(node.schedule) for node in flow.steps if node.schedule))
    if schedules:
        text += "\n定时触发：" + "；".join(schedules) + "，对满足上述入口条件的客户执行对应定时分支。"
        text += "\n定时触达与对话分支独立；定时事件只进入到期分支，对话事件只进入对应对话分支，不因收到消息补发定时内容。"
    text += "\n关闭流程后停止该流程的对话推进和自动触达，普通问答继续。"
    return text


def sop_to_canvas(scope: str, name: str, flow: SopFlowDefinition, category_names: dict[str, str], *, specialized: bool = True) -> CanvasDefinition:
    # Generated IDs avoid collisions with legacy nodes named start/end or maximum-length IDs.
    ids = {node.step_id: f"step_{index}" for index, node in enumerate(flow.steps)}
    by_id = {node.step_id: node for node in flow.steps}
    incoming = {key: [] for key in ids}
    outgoing = {key: [] for key in ids}
    for edge in flow.transitions:
        outgoing[edge.from_step].append(edge)
        if edge.to_step:
            incoming[edge.to_step].append(edge)

    nodes = [{"id": "start", "type": "start", "name": "开始", "trigger_condition": _entry_text(flow, category_names)}]
    edges = [{"id": "entry", "source": "start", "target": ids[flow.start_step_id], "label": "入口条件满足"}]
    for index, source in enumerate(flow.steps):
        entries = [f"从「{by_id[edge.from_step].name}」进入：{edge.label}" for edge in incoming[source.step_id]]
        if source.step_id == flow.start_step_id:
            entries.insert(0, "开始节点的触发事件发生，且入口条件满足。")
        if source.schedule:
            entries.append(f"仅在{_schedule_text(source.schedule)}到期时执行；发送前再次校验入口条件与流程开关。")
        if source.require_product_interest:
            entries.append("客户结合上下文明确表达想了解产品信息；普通确认、感谢或仅有偏好标签不构成产品了解意向。")

        actions = list(dict.fromkeys(text for text in [source.description, source.goal, *source.directions] if text.strip()))
        if source.type == "agent_stage":
            actions.append("进入本节点即转人工，由人工接手处理上述目标，AI 不继续代替人工推进。" if source.handoff_enabled else "由 AI 结合客户当前回复、历史对话和已有档案处理，不重复询问已知信息。")
        if scope == "service" and source.step_id == flow.start_step_id:
            actions.append("本流程的选购规则：客户明确要求选购、推荐具体商品、价格或链接时转人工；AI 不直接查商品、不推荐具体商品、不发送商品卡片。养护和通用品种知识问题继续服务，不把鉴赏偏好当成购买意向。")
        if source.type == "decision":
            actions.append("按当前事件及连线条件选择分支；对话条件同时满足时优先处理客户当前问题，定时分支独立调度。")
        if source.type == "wait":
            actions.append("等待后续节点配置的触发条件；定时分支按各自周期独立触发，不阻塞对话分支。")
        if source.schedule:
            actions.append(f"使用「{source.schedule.copy_type}」类型的素材。")
            if source.schedule.match_preferences:
                actions.append("素材须同时匹配客户喜欢的兰花品类与产品需求，缺少匹配素材则跳过本次触达并记录原因。")
            if source.schedule.purpose == "care_followup":
                actions.append("结合季节和客户养护档案进行关怀回访。")
            elif source.schedule.purpose == "personalized_knowledge":
                actions.append("结合客户地区、养兰环境、现有品种和养护痛点，发送个性化养护知识。")

        exits = []
        for edge in sorted(outgoing[source.step_id], key=lambda edge: edge.priority):
            target_name = by_id[edge.to_step].name if edge.to_step else "结束本次分支"
            exits.append(f"满足「{edge.label}」后进入「{target_name}」（优先级 {edge.priority}，数值越小越优先）。")
        if not exits:
            if source.schedule:
                exits.append("本次触达已发送，或因入口条件不再满足、无匹配素材而跳过并记录原因后，结束本次定时分支。")
            elif source.handoff_enabled:
                exits.append("已将客户需求及上下文交给人工后，结束本次自动处理分支。")
            else:
                exits.append("本节点目标已完成、当前问题已处理或客户明确结束交流后，结束本次对话分支。")
        else:
            exits.append("条件未满足时停留在当前节点；不同定时分支分别判断到期条件。")
        exits.append("结束仅结束本次分支，后续新的客户事件或下次定时到期仍按开始条件重新触发。")
        nodes.append({"id": ids[source.step_id], "type": "general", "name": source.name,
                      "trigger_condition": "\n".join(entries), "action": "\n".join(actions), "exit_condition": "\n".join(exits)})
        if not outgoing[source.step_id]:
            edges.append({"id": f"finish_{index}", "source": ids[source.step_id], "target": "end", "label": "本次分支完成"})

    for index, edge in enumerate(flow.transitions):
        edges.append({"id": f"edge_{index}", "source": ids[edge.from_step],
                      "target": ids[edge.to_step] if edge.to_step else "end", "label": edge.label})
    nodes.append({"id": "end", "type": "end", "name": "结束本次分支"})
    _layout(nodes, edges, {ids[key]: pos.y for key, pos in flow.layout.items()})
    canvas = CanvasDefinition.model_validate({"name": name, "nodes": nodes, "edges": edges})
    if specialized:
        for source, node in zip(flow.steps, canvas.nodes[1:-1]):
            if source.schedule:
                node.type = "scheduled_send"
                node.schedule_rule = _schedule_text(source.schedule)
                # Legacy material matching did not restrict the media format.
                node.send_content_type = "mixed"
            elif source.handoff_enabled:
                node.type = "handoff"
                node.handoff_target = "按现有转人工设置通知负责人"
            elif source.type == "decision" and len(outgoing[source.step_id]) >= 2:
                node.type = "condition"
        priorities = {f"edge_{index}": edge.priority for index, edge in enumerate(flow.transitions)}
        canvas.edges.sort(key=lambda edge: priorities.get(edge.id, 100))
    return canvas


def _layout(nodes: list[dict], edges: list[dict], old_y: dict[str, float]) -> None:
    # Legacy cards were shorter; preserve branch ordering while leaving room for three text fields.
    parents = {node["id"]: [edge["source"] for edge in edges if edge["target"] == node["id"]] for node in nodes}
    ranks = {"start": 0}
    def rank(key):
        if key not in ranks:
            ranks[key] = 1 + max(rank(parent) for parent in parents[key])
        return ranks[key]
    groups = {}
    for node in nodes:
        groups.setdefault(rank(node["id"]), []).append(node)
    x_shift = max(0, max(groups) * 360 - 9800)
    for depth, group in groups.items():
        y_shift = max(0, (len(group) - 1) * 320 - 9800)
        for lane, node in enumerate(sorted(group, key=lambda node: old_y.get(node["id"], 0))):
            node["position"] = {"x": 80 + depth * 360 - x_shift, "y": 80 + lane * 320 - y_shift}

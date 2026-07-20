# 首单七阶段销售流程重构实施计划

**目标：** 将《首单SOP》中的“破冰—挖需求—找痛点—推品—塑品—试成交—成交推进”落地为可解释、可追踪、可配置、可测试的销售状态机，使 AI 每轮都能回答客户当前问题，并基于已知信息执行一个明确的下一步销售动作。

**总体架构：** 保留现有 FastAPI 模块化单体、统一回复决策链、用户画像与 `active_opportunity`。新增固定的首单阶段目录和纯函数式阶段决策器；意图识别只提供客户信号与事实，不再直接决定最终阶段。阶段决策器根据当前阶段、可信业务事实、客户信号和已采集槽位输出阶段、依据和下一步动作。话术继续由话术系统提供，但按阶段、分支、适用条件和禁用条件检索。售后与人工接管作为可恢复的中断状态处理，不属于七阶段主链路。

**技术栈：** Python 3.11+、FastAPI、Pydantic 2、SQLAlchemy、pytest、Vue 3、TypeScript、Element Plus、现有用户画像、话术库、回复编排与评测框架。

---

## 一、范围与成功标准

### 本期范围

- 首单七阶段标准化。
- 阶段进入、保持、跳级、回环、完成和中断规则。
- 分阶段最小信息采集与“一轮最多追问一个问题”。
- 首单销售动作规划。
- SOP 分支话术的结构化配置、检索和安全约束。
- 用户画像、销售机会和后台监督面板展示。
- 旧阶段值兼容迁移。
- 单元测试、对话链路测试、评测集和灰度开关。

### 本期不做

- 不在本期实现售后养护 SOP 和复购 SOP 的完整状态机。
- 不重写现有 RAG、商品同步、活动、人工接管或出站队列。
- 不要求客户机械地回答所有问题后才能推进。
- 不让 AI 自行确认库存、价格、赠品、活动、付款或订单状态。
- 不把 L1-L6 客户等级合并到销售阶段。
- 不允许用虚假稀缺、虚假倒计时或无法证实的经营数据推进成交。

### 验收成功标准

- 系统只有一套首单主阶段目录，前后端展示一致。
- `decide_sales_stage()` 是最终阶段的唯一决策入口。
- 七个阶段均有明确目标、进入证据、退出证据、允许动作和禁止行为。
- 客户可以基于强信号跳级，不必机械走完全部阶段。
- 客户提出新信息或异议时，系统可以受控回环，不会被单调递增规则锁死。
- 售后、投诉、退款和人工请求会暂停销售，恢复后回到原首单阶段。
- 每轮最多追问一个关键问题，并且不重复询问已知或已问过的信息。
- “已成交”只能由可信订单/付款事实确认，不能由客户一句“我付了”或模型推断确认。
- SOP 主要分支均有可检索话术，且每条话术都有适用条件和禁用条件。
- 后台能看到当前阶段、阶段目标、阶段依据、已知信息、缺失信息、成交阻碍和下一步动作。
- 旧用户阶段可以兼容读取并逐步迁移，不出现大面积回到 `unknown`。
- 相关后端测试、前端类型检查与构建通过；七阶段评测集无阶段泄漏、虚假事实和重复追问。

---

## 二、目标业务模型

### 2.1 首单主阶段

| 顺序 | 枚举值 | 展示名称 | 阶段目标 | 典型退出证据 |
| --- | --- | --- | --- | --- |
| 1 | `rapport` | 破冰 | 让客户开口并建立自然、可信的沟通 | 客户回复有效信息或明确来意 |
| 2 | `need_discovery` | 挖需求 | 判断服务、产品或复合需求 | `need_track` 已明确，或客户给出明确购买/养护方向 |
| 3 | `pain_discovery` | 找痛点 | 明确核心困难、期望结果或购买动机 | 至少一个核心痛点、期望结果或明确产品方向 |
| 4 | `solution_recommended` | 推品 | 基于客户信息推荐可解释的产品或服务方案 | 客户愿意了解、比较或追问推荐方案 |
| 5 | `value_built` | 塑品 | 建立苗质、服务和方案价值认知 | 客户认可价值，或开始确认价格、规格、库存、下单方式 |
| 6 | `trial_close` | 试成交 | 给出可信报价/方案，降低决策难度并试探下单意愿 | 客户同意购买、明确犹豫或提出成交阻碍 |
| 7 | `closing` | 成交推进 | 解决最后顾虑并推进真实下单/付款 | 可信付款成功，或客户明确拒绝/结束本次机会 |

### 2.2 销售机会状态

销售阶段与机会结果分开保存：

```text
active    当前仍在首单推进
won       已由可信订单/付款事实确认成交
lost      客户明确拒绝，本次机会结束
paused    被售后、人工或其他高优先级服务打断
expired   长期无互动，机会过期
```

`won` 不是第八个对话阶段。成交后结束首单机会，后续进入售后服务域。

### 2.3 中断状态

以下状态不进入七阶段排序：

- `after_sale`：售后、退款、投诉、到货异常或已购养护服务。
- `human_pending`：明确转人工、高风险或必须人工查询处理。

进入中断时记录：

```json
{
  "interruption": {
    "type": "after_sale",
    "reason": "shipping_damage",
    "resume_stage": "trial_close",
    "started_at": "..."
  }
}
```

中断解除后，只有在机会仍为 `active` 时才恢复 `resume_stage`；已付款、退款、拒绝或机会过期时不恢复销售推进。

### 2.4 客户信号不是销售阶段

意图识别层输出客户信号，阶段决策层消费信号：

```text
responded              已有效回应
service_need           有养护/教学服务需求
product_need           有购花需求
combined_need          产品与服务复合需求
pain_revealed          已表达痛点
preference_revealed    已表达品种或审美偏好
recommendation_engaged 愿意了解或比较推荐
value_acknowledged     认可苗质、服务或差异
price_interest         询价或确认规格
ready_to_buy           明确下单意向
objection              存在成交阻碍
purchase_rejected      明确拒绝本次购买
purchased              可信订单/付款成功
```

同一轮可以有多个信号。`purchased` 必须来自可信业务上下文，不接受模型自由输出。

---

## 三、阶段数据契约

### 3.1 阶段定义

新增 `SalesStageDefinition`：

```python
class SalesStageDefinition(BaseModel):
    stage: str
    display_name: str
    sequence: int
    objective: str
    entry_evidence_any: list[str]
    exit_evidence_any: list[str]
    allowed_actions: list[str]
    required_slot_groups: list[list[str]]
    prohibited_behaviors: list[str]
```

`required_slot_groups` 表示“满足其中一组即可”，避免把销售聊天做成问卷。例如推品阶段可以满足：

```text
产品线：need_track + environment/region + preference/budget/pain_point
服务线：need_track + pain_point + current_care_context
复合线：need_track + pain_point + environment + preference/product_direction
```

### 3.2 销售机会

继续使用 `UserProfileModel.active_opportunity_json`，第一期不新增机会表：

```json
{
  "opportunity_id": "opp_xxx",
  "kind": "first_order",
  "status": "active",
  "current_stage": "pain_discovery",
  "previous_stage": "need_discovery",
  "stage_reason": "pain_revealed",
  "stage_evidence": ["pain_point:烂根", "need_track:service"],
  "slots": {},
  "asked_slots": [],
  "signals": [],
  "decision_blocker": null,
  "recommended_product_ids": [],
  "selected_offer": null,
  "last_sales_action": "discover_pain",
  "last_reply_goal": "确认客户最想解决的问题",
  "interruption": null,
  "started_at": "...",
  "last_active_at": "..."
}
```

`UserProfileModel.current_stage` 继续保存当前对外展示的标准阶段，用于兼容现有接口；详细依据保存在机会 JSON 中。

### 3.3 分阶段槽位

| 分类 | 槽位 | 说明 |
| --- | --- | --- |
| 基础 | `plant_count`、`experience_level`、`owned_varieties` | 养兰规模与经验 |
| 需求 | `need_track`、`desired_outcome` | 服务、产品、复合需求及期望结果 |
| 痛点 | `pain_point`、`failed_history` | 当前问题及历史失败经历 |
| 环境 | `region`、`placement`、`light`、`ventilation`、`temperature` | 推荐依据 |
| 偏好 | `color_preference`、`fragrance_preference`、`difficulty_preference`、`collection_preference` | 产品匹配依据 |
| 交易 | `budget`、`selected_product_id`、`selected_sku_id`、`quantity` | 试成交与订单准备 |
| 阻碍 | `decision_blocker` | `price/trust/care_risk/product_fit/choice/timing/other` |

槽位遵循以下原则：

- 不要求每个客户都采集全部槽位。
- 优先使用历史画像、当前消息、商品上下文和已有机会数据。
- 每轮只选择一个最影响当前动作的缺失槽位。
- 已问未答的槽位短期内不重复追问。
- 客户明确拒绝提供的信息不得反复追问。

---

## 四、目标信息流

```text
用户消息 + 历史会话 + 用户画像 + 订单/商品事实
                    |
                    v
       intent_service：意图、信号、槽位候选
                    |
                    v
       sales_stage_service：唯一阶段决策
                    |
                    v
       sales_action_service：本轮目标与一个动作
                    |
                    v
              reply_planner
                    |
                    v
       话术 / 商品事实 / RAG / 人工接管
                    |
                    v
               FinalReply
                    |
                    v
  state_service + user_profile_service 持久化机会与依据
```

阶段决策必须发生在动作决策之前；回复生成器不能自行修改阶段。

---

## 五、流转规则

### 5.1 正常前进

- 当前阶段的退出证据满足时，最多推进到证据支持的最远阶段。
- 推进必须写入 `stage_reason` 和 `stage_evidence`。
- 没有新证据时保持当前阶段，不因模型低置信输出反复切换。

### 5.2 跳级

允许以下典型跳级：

- 新客户直接说明地区、预算、环境和目标品种，可从破冰进入推品。
- 客户直接确认具体 SKU 并询问付款方式，可进入试成交或成交推进。
- 客户表达明确价格/信任/选择阻碍，可进入成交推进，但仍要保留缺失的推荐依据，不能编造适配结论。

### 5.3 受控回环

允许以下回环：

- 推品后客户补充了关键环境信息，重新执行推荐动作，但阶段可保持推品。
- 塑品或试成交时出现新的核心痛点，可回到找痛点或推品。
- 成交推进解决阻碍后，回到试成交重新确认方案。
- 客户更换目标品种或预算发生实质变化时，创建新机会或回到推品。

禁止仅依据弱问候、普通养护问题或低置信模型输出回退阶段。

### 5.4 高优先级覆盖

优先级从高到低：

1. 退款、投诉、赔付、明确转人工和高风险。
2. 可信订单/付款状态。
3. 当前客户问题的直接回答。
4. 当前首单阶段目标。
5. 销售追问或成交推进。

### 5.5 完成与关闭

- `ready_to_buy` 只表示意向，机会仍为 `active`。
- 可信 `payment_success` 或 `order_completed` 才将机会置为 `won`。
- 客户明确“不买、不要推荐、结束沟通”时置为 `lost`。
- 长期无互动按现有 30 天逻辑置为 `expired`，具体阈值保持可配置。

---

## 六、阶段动作与话术策略

| 阶段 | 默认动作 | 首选问题/输出 | 禁止行为 |
| --- | --- | --- | --- |
| 破冰 | `build_rapport` | 回应来意，收集一个基础信息 | 连续抛多个问题、立即硬推产品 |
| 挖需求 | `discover_need_track` | 判断服务/产品/复合需求 | 未回答当前问题就开始问卷 |
| 找痛点 | `discover_pain` | 确认核心困难或期望结果 | 夸大损失、下医疗式确定诊断 |
| 推品 | `recommend_solution` | 给出 1-2 个有依据的方案 | 无商品事实推荐、堆砌大量商品 |
| 塑品 | `build_value` | 解释苗质、服务和适配价值 | 编造销量、苗质、服务承诺 |
| 试成交 | `trial_close` | 可信报价、二选一或确认方案 | 未获得价格事实就报价格 |
| 成交推进 | `resolve_blocker` / `close_order` | 针对一个阻碍并确认下一步 | 虚假稀缺、重复施压、假装已下单 |

话术选择顺序：

```text
阶段匹配
→ 当前动作匹配
→ 客户信号/阻碍匹配
→ 必要条件满足
→ 禁用条件未命中
→ 商品与经营事实可验证
→ 优先级和版本排序
```

---

## 七、文件改造地图

### 后端新增

- `wechat_rag_bot/app/schemas/sales_flow.py`
  - 阶段定义、阶段证据、阶段决策、客户信号和中断结构。
- `wechat_rag_bot/app/services/sales_stage_catalog.py`
  - 七阶段固定目录、目标、槽位组和禁止行为。
- `wechat_rag_bot/app/services/sales_signal_service.py`
  - 将意图、槽位、标签和可信业务事实归一为客户信号。
- `wechat_rag_bot/tests/test_sales_stage_catalog.py`
- `wechat_rag_bot/tests/test_sales_signal_service.py`
- `wechat_rag_bot/tests/test_first_order_sales_flow.py`
- `wechat_rag_bot/app/routers/admin_sales_flow.py`
  - 阶段目录、客户当前阶段详情和人工调整接口。

### 后端修改

- `wechat_rag_bot/app/services/tag_catalog.py`
  - 替换旧销售阶段目录，增加旧值别名兼容。
- `wechat_rag_bot/app/services/intent_service.py`
  - 补充信号与槽位抽取；降低 LLM `sales_stage` 的最终决策权。
- `wechat_rag_bot/app/services/sales_stage_service.py`
  - 重写为唯一、可解释的阶段状态机。
- `wechat_rag_bot/app/services/sales_action_service.py`
  - 改为按阶段选择动作、缺失槽位和下一步目标。
- `wechat_rag_bot/app/services/chat_orchestrator.py`
  - 注入归一信号、阶段决策和动作决策。
- `wechat_rag_bot/app/services/prompt_builder.py`
  - 注入阶段目标、已知事实、唯一追问槽位和禁止行为。
- `wechat_rag_bot/app/services/state_service.py`
  - 持久化阶段依据、信号、槽位和中断状态。
- `wechat_rag_bot/app/services/user_profile_service.py`
  - 更新机会摘要、跟进建议和旧阶段兼容。
- `wechat_rag_bot/app/schemas/template.py`
  - 增加阶段分支、条件、禁用条件和事实要求。
- `wechat_rag_bot/app/services/template_service.py`
  - 逐步取消内存默认话术作为主数据源。
- `wechat_rag_bot/app/talk_script/models.py`
- `wechat_rag_bot/app/talk_script/repository.py`
- `wechat_rag_bot/app/talk_script/service.py`
  - 支持按阶段和动作过滤数据库话术。
- `wechat_rag_bot/app/db/models.py`
  - 为 `TemplateLibraryModel` 补充结构化销售话术字段。
- `wechat_rag_bot/app/main.py`
  - 注册销售流程管理接口。

### 前端新增

- `admin-web/src/api/admin/sales-flow.ts`
- `admin-web/src/views/sales-flow/index.vue`
  - 七阶段目录、阶段规则和话术覆盖概览。
- `admin-web/src/views/sales-scripts/index.vue`
  - 销售话术列表、编辑和启停。
- `admin-web/src/views/workbench/components/SalesOpportunityPanel.vue`
  - 当前机会、阶段依据、缺失信息、阻碍和下一步动作。

### 前端修改

- `admin-web/src/router/index.ts`
  - 新增首单流程页并替换销售话术占位页。
- `admin-web/src/layouts/SalesLayout.vue`
  - SOP 菜单增加“首单流程”。
- `admin-web/src/utils/tagDisplay.ts`
  - 统一七阶段中文名称，保留旧值兼容显示。
- `admin-web/src/api/user-profile/index.ts`
  - 增加机会详情类型。
- `admin-web/src/views/workbench/components/SupervisionPanel.vue`
  - 嵌入销售机会面板。

---

## 八、任务拆解

### Task 1：冻结阶段契约与兼容映射

**文件：**

- 新增：`wechat_rag_bot/app/schemas/sales_flow.py`
- 新增：`wechat_rag_bot/app/services/sales_stage_catalog.py`
- 修改：`wechat_rag_bot/app/services/tag_catalog.py`
- 修改：`admin-web/src/utils/tagDisplay.ts`
- 新增测试：`wechat_rag_bot/tests/test_sales_stage_catalog.py`

- [x] 定义七阶段枚举、中文名称、顺序、目标、证据和允许动作。
- [x] 定义非阶段的 `after_sale`、`human_pending` 中断类型。
- [x] 建立旧值映射：

```text
unknown              -> rapport（仅新首单机会）或保持 unknown
greeting             -> rapport
need_discovery       -> need_discovery
pain_confirmed       -> pain_discovery
solution_recommended -> solution_recommended
price_discussed      -> trial_close
objection_handling   -> closing
order_intent         -> closing + ready_to_buy
after_sale           -> interruption.after_sale
human_pending        -> interruption.human_pending
```

- [x] 明确 `first_order_nurture` 等历史展示值只用于兼容，不再允许新写入。
- [x] 测试目录中不存在重复顺序、未知动作或缺失中文名称。
- [x] 测试所有旧值都能稳定归一，不会抛异常。

**验证：**

```powershell
cd wechat_rag_bot
python -m pytest tests/test_sales_stage_catalog.py tests/test_admin_tags.py -q
```

### Task 2：归一客户信号与销售槽位

**文件：**

- 新增：`wechat_rag_bot/app/services/sales_signal_service.py`
- 修改：`wechat_rag_bot/app/services/intent_service.py`
- 修改：`wechat_rag_bot/app/schemas/intent.py`
- 修改：`wechat_rag_bot/app/services/user_profile_service.py`
- 新增测试：`wechat_rag_bot/tests/test_sales_signal_service.py`

- [x] 在 `IntentResult` 中增加结构化 `sales_signals: list[str]`，保留 `sales_stage` 兼容字段。
- [x] 从当前消息、历史上下文、现有槽位、标签、商品事实和订单事实归一信号。
- [x] 扩充并规范槽位名称，避免同一事实出现多个键名。
- [x] 将 `decision_blocker` 扩展为 `price/trust/care_risk/product_fit/choice/timing/other`。
- [x] 对 `purchased` 做可信来源校验：仅订单工具状态、支付回调或已验证订单快照可生成。
- [x] 客户口述“已经付款”生成 `payment_claimed`，不得直接生成 `purchased`。
- [x] 测试服务、产品、复合需求以及典型成交阻碍。
- [x] 测试养护咨询不会自动变成售后，除非存在已购事实或售后请求。

**验证：**

```powershell
python -m pytest tests/test_sales_signal_service.py tests/test_intent_service.py tests/test_user_profile_service.py -q
```

### Task 3：重写阶段状态机

**文件：**

- 修改：`wechat_rag_bot/app/services/sales_stage_service.py`
- 修改测试：`wechat_rag_bot/tests/test_sales_stage_service.py`
- 新增测试：`wechat_rag_bot/tests/test_first_order_sales_flow.py`

- [x] 将阶段输入统一为：当前机会、客户信号、槽位、意图、标签和可信业务事实。
- [x] 输出 `previous_stage`、`stage`、`reason`、`evidence`、`transition_type`。
- [x] 实现正常前进、强信号跳级、受控回环、保持、中断、恢复和关闭。
- [x] 删除当前仅按数字大小禁止回退的单调递增实现。
- [x] 保留弱信号防抖：普通问候、单次知识问题不改变成熟销售机会阶段。
- [x] 价格问题在需求不清楚时保持挖需求；在推荐有依据后可进入试成交。
- [x] 异议可以进入成交推进；阻碍解决后允许回到试成交。
- [x] 换品种或预算实质变化时标记新机会/重新推荐，而不是沿用错误上下文。
- [x] 售后和人工中断保留 `resume_stage`。

**核心测试场景：**

- [x] 问候 → 挖需求 → 找痛点 → 推品 → 塑品 → 试成交 → 成交推进。
- [x] 新客带齐地区、预算、偏好，直接进入推品。
- [x] 未知需求直接问价，回答后仍停留挖需求。
- [x] 塑品阶段补充环境不匹配，回到推品重新推荐。
- [x] 成交推进解决异议，回到试成交。
- [x] 退款/投诉暂停销售；恢复时回到原阶段。
- [x] 客户口述付款不关闭机会；可信付款事实关闭为 `won`。

**验证：**

```powershell
python -m pytest tests/test_sales_stage_service.py tests/test_first_order_sales_flow.py -q
```

### Task 4：按阶段重构销售动作规划

**文件：**

- 修改：`wechat_rag_bot/app/services/sales_action_service.py`
- 修改：`wechat_rag_bot/app/services/prompt_builder.py`
- 修改测试：`wechat_rag_bot/tests/test_sales_action_service.py`
- 修改测试：`wechat_rag_bot/tests/test_rag_service.py`

- [x] 将默认动作扩展为 `build_rapport`、`discover_need_track`、`discover_pain`、`recommend_solution`、`build_value`、`trial_close`、`resolve_blocker`、`close_order`。
- [x] 依据阶段目录的槽位组计算“当前最关键缺失信息”。
- [x] 明确问题优先级：客户当前问题 > 安全信息 > 当前阶段唯一缺失槽位。
- [x] 已知槽位不再询问，`asked_slots` 中短期未回答的槽位不重复询问。
- [x] 模板回复与 RAG 回复都遵循一轮最多一个追问，不只约束模板路径。
- [x] 将阶段目标、动作、已知事实、唯一问题和禁止行为注入 prompt。
- [x] 避免直接把内部 JSON、枚举值和阶段推理发给客户。

**验证：**

```powershell
python -m pytest tests/test_sales_action_service.py tests/test_rag_service.py tests/test_prompt_builder.py -q
```

### Task 5：建立分阶段话术库

**文件：**

- 修改：`wechat_rag_bot/app/db/models.py`
- 修改：`wechat_rag_bot/app/schemas/template.py`
- 修改：`wechat_rag_bot/app/talk_script/models.py`
- 修改：`wechat_rag_bot/app/talk_script/repository.py`
- 修改：`wechat_rag_bot/app/talk_script/service.py`
- 修改：`wechat_rag_bot/app/services/template_service.py`
- 修改测试：`wechat_rag_bot/tests/test_talk_script.py`
- 修改测试：`wechat_rag_bot/tests/test_contracts.py`

- [x] 为数据库话术增加：`sales_stage`、`sales_action`、`branch_code`、`required_conditions_json`、`exclude_conditions_json`、`required_fact_keys_json`、`variables_json`、`priority`。
- [x] 使用项目现有兼容建表/补列方式升级，不引入第二套迁移框架。
- [x] 将 Word SOP 中七阶段的主要分支录入数据库种子或可重复导入文件。
- [x] 每条话术标明客户信号、阻碍类型、必要事实和禁用条件。
- [x] 经营声明增加事实门：服务人数、苗质、无激素、全年服务、库存、价格、赠品和活动。
- [x] 对药剂名称和苗情判断设置安全边界，不使用固定话术给出确定诊断或危险配药建议。
- [x] 逐步移除 `template_service.py` 内存默认话术的主数据职责；保留最小故障兜底。
- [x] 话术未匹配时回到受约束的 LLM/RAG，不因普通销售场景直接转人工。

**首批话术覆盖：**

- [x] 破冰欢迎与基础信息采集。
- [x] 服务/产品/复合需求识别。
- [x] 新手、养不好、黄叶焦尖、不开花、烂根病害、选错品种、无明显痛点。
- [x] 服务推荐、明确品种、偏好推荐、预算有限、产品+教学。
- [x] 服务价值、苗质价值、养不活顾虑、同行价格对比。
- [x] 直接试单、二选一、养护信心、订单方案确认。
- [x] 价格、信任、选择、考虑、比价、真实权益、付款临门动作。

**验证：**

```powershell
python -m pytest tests/test_talk_script.py tests/test_contracts.py -q
```

### Task 6：接入统一回复链路与持久化

**文件：**

- 修改：`wechat_rag_bot/app/services/chat_orchestrator.py`
- 修改：`wechat_rag_bot/app/services/state_service.py`
- 修改：`wechat_rag_bot/app/services/user_profile_service.py`
- 修改：`wechat_rag_bot/app/services/context_selector.py`
- 修改测试：`wechat_rag_bot/tests/test_chat_orchestrator_reply_graph.py`
- 修改测试：`wechat_rag_bot/tests/test_user_profile_api.py`

- [x] 在统一回复计划生成前完成信号、阶段和动作决策。
- [x] 将阶段决策及证据写入回复 metadata，便于日志和后台解释。
- [x] 在 `state_service` 与 `user_profile_service` 中幂等合并槽位、信号和阶段。
- [x] 防止同步状态与持久化画像对同一机会重复推进。
- [x] 关闭机会后不再沿用旧 `asked_slots`、阻碍和推荐商品。
- [x] 新机会保留与用户长期画像相关的地区、经验和稳定偏好，但不继承旧交易阻碍。
- [x] 确保售后优先、人工静默接管和退款契约不变。

**验证：**

```powershell
python -m pytest tests/test_chat_orchestrator_reply_graph.py tests/test_user_profile_api.py tests/test_state_service.py -q
```

### Task 7：提供销售流程与人工调整 API

**文件：**

- 新增：`wechat_rag_bot/app/routers/admin_sales_flow.py`
- 修改：`wechat_rag_bot/app/main.py`
- 修改：`wechat_rag_bot/app/routers/admin_conversations.py`
- 新增测试：`wechat_rag_bot/tests/test_admin_sales_flow.py`
- 修改测试：`wechat_rag_bot/tests/test_admin_conversations.py`

- [x] `GET /api/admin/sales-flow/stages`：返回七阶段定义和话术覆盖数。
- [x] `GET /api/admin/sales-flow/opportunities/{user_id}`：返回当前机会详情。
- [x] `PATCH /api/admin/sales-flow/opportunities/{user_id}/stage`：人工调整阶段，必须记录操作人和原因。
- [x] `POST /api/admin/sales-flow/opportunities/{user_id}/close`：人工标记 lost/expired；不得人工伪造支付成功。
- [x] 会话详情接口返回当前阶段、阶段目标、依据、缺失槽位、阻碍和下一步动作。
- [x] 所有人工调整写入操作日志和更新时间。
- [x] 测试非法阶段、缺失原因、已关闭机会、售后中断和越权修改。

**验证：**

```powershell
python -m pytest tests/test_admin_sales_flow.py tests/test_admin_conversations.py -q
```

### Task 8：建设后台首单流程与销售机会面板

**文件：**

- 新增：`admin-web/src/api/admin/sales-flow.ts`
- 新增：`admin-web/src/views/sales-flow/index.vue`
- 新增：`admin-web/src/views/sales-scripts/index.vue`
- 新增：`admin-web/src/views/workbench/components/SalesOpportunityPanel.vue`
- 修改：`admin-web/src/router/index.ts`
- 修改：`admin-web/src/layouts/SalesLayout.vue`
- 修改：`admin-web/src/views/workbench/components/SupervisionPanel.vue`
- 修改：`admin-web/src/api/user-profile/index.ts`
- 测试：按现有前端测试能力补充组件测试；至少运行类型检查和构建。

- [x] 首单流程页按顺序展示七阶段、目标、进入条件、退出条件和话术覆盖数。
- [x] 销售话术页支持按阶段、动作、分支、状态筛选。
- [x] 工作台展示当前阶段、上一步、阶段依据和目标。
- [x] 展示已知信息、缺失信息、成交阻碍、推荐商品和下一步动作。
- [x] 支持人工调整阶段，但要求填写原因并二次确认。
- [x] `after_sale` 和 `human_pending` 显示为中断横幅，不伪装成主阶段节点。
- [x] 旧阶段值仍能显示友好中文。

**验证：**

```powershell
cd admin-web
pnpm exec vue-tsc --noEmit
pnpm build
```

### Task 9：迁移现有用户与灰度兼容

**文件：**

- 修改：`wechat_rag_bot/app/services/sales_stage_service.py`
- 修改：`wechat_rag_bot/app/services/user_profile_service.py`
- 新增：`wechat_rag_bot/app/services/sales_stage_migration_service.py`
- 新增测试：`wechat_rag_bot/tests/test_sales_stage_migration.py`
- 修改：`wechat_rag_bot/app/config.py`

- [x] 增加 `first_order_sales_flow_v2_enabled` 灰度开关，默认先在测试环境启用。
- [x] 读取旧阶段时动态归一，写入时只写新阶段。
- [x] 提供幂等迁移函数，批量迁移 `current_stage` 和 `active_opportunity_json`。
- [x] `order_intent` 迁移为 `closing + ready_to_buy`，不能迁移成 `won`。
- [x] `after_sale`、`human_pending` 迁移为中断结构，并尽可能保留原阶段作为恢复点。
- [x] 迁移前后输出数量统计，不记录客户敏感对话正文。
- [x] 灰度期间日志同时记录旧判定和新判定差异，但只执行开关选中的版本。
- [x] 回滚只切换读写逻辑，不删除新机会数据。

**验证：**

```powershell
python -m pytest tests/test_sales_stage_migration.py tests/test_sales_stage_service.py -q
```

### Task 10：评测、观测与上线门槛

**文件：**

- 新增：`docs/evaluation/first_order_sales_flow/README.md`
- 新增：`docs/evaluation/first_order_sales_flow/single_turn.jsonl`
- 新增：`docs/evaluation/first_order_sales_flow/multi_turn.jsonl`
- 新增：`docs/evaluation/first_order_sales_flow/boundary.jsonl`
- 修改：现有评测 runner，使结果包含阶段、动作和事实安全指标。

- [x] 为七阶段各建立正常、跳级、回环和信息不足样本。
- [x] 增加服务需求、产品需求、复合需求样本。
- [x] 增加价格、信任、养护风险、产品适配、选择困难和时机阻碍样本。
- [x] 增加付款口述与可信付款事实对照样本。
- [x] 增加售后、退款、投诉、人工和危险药剂边界样本。
- [x] 增加重复追问、无依据推荐、内部标签泄漏、虚假事实和虚假稀缺检测。
- [x] 记录阶段准确率、动作准确率、槽位重复询问率、事实幻觉率、错误成交率和人工接管准确率。

**上线门槛：**

- 七阶段关键路径用例全部通过。
- 错误成交率为 0。
- 虚假价格、库存、权益和付款事实为 0。
- 售后/退款/投诉错误进入销售推进为 0。
- 单轮超过一个新增追问的比例为 0。
- 内部枚举、阶段推理和原始业务字段泄漏为 0。
- 前端构建通过，后端相关测试通过。
- 在测试账号完成至少一轮人工对话验收后再扩大灰度。

---

## 九、关键测试矩阵

| 场景 | 当前阶段 | 输入/事实 | 预期阶段 | 预期动作 |
| --- | --- | --- | --- | --- |
| 新客问候 | unknown | “你好，在吗” | 破冰 | 自然回应并问一个基础问题 |
| 直接问价格但信息不足 | 破冰 | “多少钱” | 挖需求 | 回答可信价格后问一个适配问题 |
| 养不好 | 挖需求 | “总是烂根” | 找痛点 | 先给安全方向，再确认关键背景 |
| 明确购买条件 | 破冰 | 四川、阳台、预算 200、喜欢香 | 推品 | 推荐 1-2 个有依据的方案 |
| 接受推荐 | 推品 | “这个不错，有什么优势” | 塑品 | 解释真实价值与差异 |
| 询价下单 | 塑品 | “这个规格多少钱” | 试成交 | 报可信价格并试单 |
| 嫌贵 | 试成交 | “有点贵” | 成交推进 | 只处理价格阻碍并确认是否接受 |
| 异议解决 | 成交推进 | “那就按你说的这款” | 试成交/成交推进 | 确认规格数量并推进下单 |
| 客户声称付款 | 成交推进 | “我已经付了”但无订单事实 | 成交推进 | 查询/确认，不标记成交 |
| 支付回调成功 | 成交推进 | payment_success | 机会 won | 完成服务交接 |
| 到货破损 | 任意 | 已购+破损照片诉求 | 售后中断 | 暂停销售并收集售后证据 |
| 明确转人工 | 任意 | “给我人工” | 人工中断 | 静默人工接管 |
| 客户拒绝 | 任意 | “不要推荐了，先不买” | 机会 lost | 尊重决定，停止销售追问 |

---

## 十、风险与控制措施

### 阶段判断过度依赖模型

控制：模型只输出候选信号和槽位；最终阶段由确定性服务结合历史状态和可信事实决定。

### 阶段过细导致回复僵硬

控制：阶段约束动作，不规定逐字回复；允许跳级、保持和回环，不要求补齐所有槽位。

### 旧数据与新枚举冲突

控制：先兼容读取、双判定观测，再逐步迁移；不直接批量覆盖原数据。

### 话术包含不可验证声明

控制：声明类变量必须绑定经营或商品事实；缺失事实时换用保守表达，不允许模型补全。

### 将客户口述误判为成交

控制：`payment_claimed` 与 `purchased` 分离；只有可信订单/支付来源能关闭机会。

### 销售推进压过售后与风险

控制：售后、退款、投诉、人工和危险用药始终高于销售阶段目标。

### 人工调整破坏状态一致性

控制：人工调整必须记录操作人、原因、前后阶段和时间；不提供人工伪造支付成功能力。

---

## 十一、建议发布顺序

### 第一批：后端决策闭环

完成 Task 1-4，只启用新阶段目录、信号、状态机和动作规划；话术仍可使用现有系统兜底。目标是先保证“阶段判得对、下一步做得对”。

### 第二批：SOP 话术与持久化

完成 Task 5-6，将 Word SOP 的分支话术结构化，并让机会状态、阶段依据和阻碍稳定落库。

### 第三批：运营后台

完成 Task 7-8，让销售和运营人员能看见、理解并必要时纠正 AI 的阶段判断。

### 第四批：迁移与灰度

完成 Task 9-10，在测试账号双判定观测，通过上线门槛后逐步扩大流量。

---

## 十二、最终交付物

- 七阶段首单销售状态机及统一阶段目录。
- 结构化客户信号、槽位、阶段证据和销售动作。
- 可恢复的售后/人工中断机制。
- 分阶段 SOP 话术库与事实安全门。
- 用户画像中的结构化首单机会。
- 首单流程管理页、话术管理页和工作台机会面板。
- 旧阶段迁移与灰度开关。
- 七阶段单元测试、链路测试和专项评测集。

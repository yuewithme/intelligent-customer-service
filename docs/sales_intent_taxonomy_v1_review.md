# 销售型意图标签体系 V1.0（评审稿）

> 历史状态：该标签体系不再参与生产路由，本评审稿仅保留为离线分析参考。当前生产入口见 [Sales Agent Harness V2](agent-harness-v2/README.md)。
>
> 适用范围：用户进线、需求发现、商品推荐、价值建立、异议处理、成交推进与必要的客户服务兜底
>
> 不包含：销售路由、销售阶段本身、回复策略、情绪和风险等级。这些由后续状态机与策略层根据标签结果判断。

## 1. 评审方法

请为每个标签选择一种处理结果：

- [ ] 保留
- [ ] 改名
- [ ] 与其他标签合并
- [ ] 删除
- [ ] 需要拆分

每张标签卡都预留以下内容：

- 中文名与定义
- 应当命中的边界
- 不应命中的边界
- 初始正面例子
- 初始反面例子
- 易混淆标签
- 建议提取的槽位
- 可能产生的销售信号和阶段影响
- 业务评审意见与待补充样例

评审时优先判断三件事：

1. 这个标签是否会让系统采取不同的销售动作或服务动作。
2. 业务人员能否稳定地区分它与相邻标签。
3. 是否能找到足够多、足够多样的真实用户表达。

如果不能满足以上条件，优先合并而不是继续细分。

## 2. 标注总原则

### 2.1 Domain、Goal、Issue 的职责

- **Domain**：用户当前正在谈购买链路的哪一部分。
- **Goal**：用户当前想完成什么、表达什么行为。
- **Issue**：影响需求、选品、购买或服务的具体主题。
- **Slot**：用户说出的具体值，例如地区、预算、花色、数量，不作为标签继续扩张。

### 2.2 多意图输出

- `primary_domain` 只能有一个；必要时允许 `secondary_domains`。
- `primary_goal` 只能有一个；必要时允许 `secondary_goals`。
- `issues` 可以多选，也可以为空。
- 主 Goal 选择用户最明确希望系统优先完成的动作，不按路由风险机械排序。

### 2.3 售前保障与真实售后必须区分

- “这个包邮吗、坏了能退吗”属于售前购买决策，通常是 `commercial_decision`。
- “昨天收到的苗断了、我要退款”属于已发生的服务问题，通常是 `customer_service`。
- 是否已经存在真实订单，是区分两者的重要业务事实。

### 2.4 样例建设最低建议

评审通过后，每个标签至少需要：

- 10～20 条真实或人工确认的正例；
- 5～10 条与相邻标签容易混淆的反例；
- 至少 3 条带否定、指代、省略或上下文依赖的难例；
- 高风险服务标签需要额外覆盖投诉、退款和明确人工请求。

## 3. 标签总览

### 3.1 Domain（8 个）

| 编码 | 中文名 |
|---|---|
| `customer_need` | 客户需求 |
| `product_solution` | 商品与解决方案 |
| `care_service` | 养护服务 |
| `commercial_decision` | 价格与购买决策 |
| `purchase_transaction` | 购买与成交 |
| `customer_service` | 客户服务处理 |
| `conversation` | 普通会话 |
| `out_of_scope` | 范围外 |

### 3.2 Goal（24 个）

| 分组 | 编码 |
|---|---|
| 需求与咨询 | `express_need`, `provide_information`, `ask_information`, `seek_recommendation`, `compare_options`, `seek_solution` |
| 购买决策 | `express_interest`, `express_objection`, `negotiate`, `confirm_choice`, `defer_decision`, `decline_purchase` |
| 成交动作 | `purchase`, `pay`, `query_status` |
| 服务兜底 | `request_service`, `request_refund_return`, `complain`, `request_human` |
| 会话行为 | `affirm`, `deny`, `social`, `end_conversation`, `unclear` |

### 3.3 Issue（28 个）

| 分组 | 编码 |
|---|---|
| 客户需求与画像 | `purchase_purpose`, `experience_level`, `growing_environment`, `preference`, `budget`, `pain_and_outcome` |
| 商品与推荐 | `product_information`, `recommendation_fit`, `product_comparison`, `stock_availability` |
| 养护服务 | `care_general`, `watering_fertilizing`, `environment_care`, `medium_repotting`, `symptom_disease_recovery` |
| 商业决策与阻碍 | `price`, `discount`, `product_value`, `quality_trust`, `care_confidence`, `service_guarantee` |
| 下单与支付 | `sku_quantity`, `order_process`, `payment` |
| 精简客户服务 | `delivery`, `after_sale_policy`, `received_problem`, `refund_return` |

---

# 4. Domain 标签评审卡

## D01 `customer_need`｜客户需求

- **评审结果**：[ ] 保留 [ ] 改名 [ ] 合并 [ ] 删除 [ ] 拆分
- **定义**：用户描述购买用途、养护条件、预算、偏好、经验、困难或期望结果，核心在“客户需要什么”。
- **应当命中**：自养/送礼用途；地区和摆放环境；新手或有经验；预算；颜色与香味偏好；过去养不活等困难。
- **不应命中**：只询问具体商品参数；只问价格；已经明确要求下单或支付。
- **初始正例**：
  1. “我是新手，想找一盆比较好养的。”
  2. “广东阳台养，预算三百左右。”
  3. 【待补充真实正例】
- **初始反例**：
  1. “这盆是什么品种？”（更偏 `product_solution`）
  2. “这个多少钱？”（更偏 `commercial_decision`）
  3. 【待补充易混淆反例】
- **易混淆标签**：`product_solution`, `care_service`
- **建议槽位**：`purchase_purpose`, `region`, `placement`, `experience_level`, `budget_min`, `budget_max`, `color_preference`, `fragrance_preference`, `pain_point`, `desired_outcome`
- **销售信号/阶段影响**：`product_need`, `service_need`, `preference_revealed`, `pain_revealed`, `desired_outcome`；主要支持挖需求和找痛点。
- **业务评审意见**：【待填写】
- **新增正例**：【待填写】
- **新增反例**：【待填写】

## D02 `product_solution`｜商品与解决方案

- **评审结果**：[ ] 保留 [ ] 改名 [ ] 合并 [ ] 删除 [ ] 拆分
- **定义**：用户了解商品事实、请求推荐、比较商品，或判断某个商品是否适合自己的需求。
- **应当命中**：商品品种/花色/苗情/规格；推荐；商品对比；是否适合新手、地区或用途；库存选择。
- **不应命中**：只描述个人情况但未讨论商品；单纯问养护操作；只谈价格优惠。
- **初始正例**：
  1. “给新手推荐一盆好养又香的。”
  2. “这两款有什么区别，哪款更适合北方？”
  3. 【待补充真实正例】
- **初始反例**：
  1. “我在北京室内养。”（更偏 `customer_need`）
  2. “多久浇一次水？”（更偏 `care_service`）
  3. 【待补充易混淆反例】
- **易混淆标签**：`customer_need`, `care_service`, `commercial_decision`
- **建议槽位**：`product_id`, `sku_id`, `variety`, `color`, `seedling_size`, `stock_status`, `comparison_product_ids`
- **销售信号/阶段影响**：`recommendation_ready`, `recommendation_engaged`, `selected_product`；主要支持推品和塑品。
- **业务评审意见**：【待填写】
- **新增正例**：【待填写】
- **新增反例**：【待填写】

## D03 `care_service`｜养护服务

- **评审结果**：[ ] 保留 [ ] 改名 [ ] 合并 [ ] 删除 [ ] 拆分
- **定义**：用户询问兰花养护、异常处理或购买后的持续指导，也包括因担心养不好而产生的服务需求。
- **应当命中**：浇水施肥；光照温湿度；植料换盆；黄叶烂根；病虫害；是否有人指导；担心养死。
- **不应命中**：询问商品自身的规格；纯价格异议；已经发生的到货损坏或退款问题。
- **初始正例**：
  1. “这个平时要怎么养？”
  2. “我怕买回去养不活，有没有人指导？”
  3. 【待补充真实正例】
- **初始反例**：
  1. “这盆苗多大？”（商品信息）
  2. “收到的时候根已经断了。”（真实服务问题）
  3. 【待补充易混淆反例】
- **易混淆标签**：`customer_need`, `product_solution`, `customer_service`
- **建议槽位**：`plant_variety`, `symptom`, `care_action`, `region`, `placement`, `temperature`, `humidity`
- **销售信号/阶段影响**：可能产生 `service_need`, `care_concern`, `pain_revealed`；纯知识问题保持阶段，购买顾虑进入异议处理。
- **业务评审意见**：【待填写】
- **新增正例**：【待填写】
- **新增反例**：【待填写】

## D04 `commercial_decision`｜价格与购买决策

- **评审结果**：[ ] 保留 [ ] 改名 [ ] 合并 [ ] 删除 [ ] 拆分
- **定义**：用户评估是否购买，关注价格、优惠、商品价值、品质信任、养护信心和服务保障。
- **应当命中**：询价、议价、优惠；觉得贵；为什么值这个价；真假和品质；怕养不好；售前询问包邮或坏了怎么办。
- **不应命中**：已经发生的物流或售后问题；明确下单和支付；单纯请求商品推荐。
- **初始正例**：
  1. “这个有点贵，能优惠吗？”
  2. “能保证对版吗，收到坏了怎么处理？”
  3. 【待补充真实正例】
- **初始反例**：
  1. “我的快递三天没动了。”（真实客户服务）
  2. “就要这个，给我两盆。”（购买成交）
  3. 【待补充易混淆反例】
- **易混淆标签**：`product_solution`, `care_service`, `purchase_transaction`, `customer_service`
- **建议槽位**：`quoted_price`, `budget`, `requested_discount`, `decision_blockers`, `guarantee_topic`
- **销售信号/阶段影响**：`price_interest`, `value_acknowledged`, `objection`, `care_concern`；主要支持塑品、试成交和成交推进。
- **业务评审意见**：【待填写】
- **新增正例**：【待填写】
- **新增反例**：【待填写】

## D05 `purchase_transaction`｜购买与成交

- **评审结果**：[ ] 保留 [ ] 改名 [ ] 合并 [ ] 删除 [ ] 拆分
- **定义**：用户确认商品、规格和数量，准备下单、索要购买入口或进行支付。
- **应当命中**：明确选择某款；确认颜色规格；购买数量；怎么下单；购买链接；支付方式；声称已付款。
- **不应命中**：仍在比较商品；只询问价格；订单发生后的物流或售后问题。
- **初始正例**：
  1. “就要刚才那个红色的，给我两盆。”
  2. “发个购买链接，我现在付款。”
  3. 【待补充真实正例】
- **初始反例**：
  1. “红色和黄色哪个好？”（仍在比较）
  2. “这个多少钱？”（询价）
  3. 【待补充易混淆反例】
- **易混淆标签**：`product_solution`, `commercial_decision`, `customer_service`
- **建议槽位**：`product_id`, `sku_id`, `color`, `specification`, `quantity`, `purchase_link_requested`, `payment_method`
- **销售信号/阶段影响**：`selected_product`, `ready_to_buy`, `payment_claimed`；主要支持试成交和成交推进。成交必须由订单事实确认。
- **业务评审意见**：【待填写】
- **新增正例**：【待填写】
- **新增反例**：【待填写】

## D06 `customer_service`｜客户服务处理

- **评审结果**：[ ] 保留 [ ] 改名 [ ] 合并 [ ] 删除 [ ] 拆分
- **定义**：用户针对已经发生的订单、配送、收货或售后问题请求处理，是销售体系外的精简服务兜底。
- **应当命中**：查询真实订单/物流；到货损坏；错发少发；退货退款；明确投诉；人工介入。
- **不应命中**：购买前询问包邮、发货时效或售后保障；单纯询问养护知识。
- **初始正例**：
  1. “昨天收到的苗断了，帮我处理一下。”
  2. “我的订单还没发货，帮我查一下。”
  3. 【待补充真实正例】
- **初始反例**：
  1. “这个包邮吗？”（购买决策）
  2. “买回去养死有保障吗？”（售前保障顾虑）
  3. 【待补充易混淆反例】
- **易混淆标签**：`commercial_decision`, `care_service`
- **建议槽位**：`order_id`, `tracking_no`, `service_problem`, `requested_resolution`, `received_at`
- **销售信号/阶段影响**：产生 `after_sale_interruption` 或 `human_interruption`；暂停活跃销售机会，处理完成后恢复。
- **业务评审意见**：【待填写】
- **新增正例**：【待填写】
- **新增反例**：【待填写】

## D07 `conversation`｜普通会话

- **评审结果**：[ ] 保留 [ ] 改名 [ ] 合并 [ ] 删除 [ ] 拆分
- **定义**：没有独立业务主题的寒暄、确认、否认、感谢和结束表达。
- **应当命中**：你好、在吗、好的、不是、谢谢、不用了、再见。
- **不应命中**：消息中虽然有寒暄，但同时包含明确业务需求；此时业务 Domain 应为主。
- **初始正例**：
  1. “你好，在吗？”
  2. “好的，谢谢。”
  3. 【待补充真实正例】
- **初始反例**：
  1. “你好，这盆多少钱？”（主域为购买决策）
  2. “好的，我在广东阳台养。”（主域为客户需求）
  3. 【待补充易混淆反例】
- **易混淆标签**：所有业务 Domain，尤其是带寒暄前缀的消息
- **建议槽位**：通常为空
- **销售信号/阶段影响**：通常保持当前阶段；实质性确认由 Goal 和上下文进一步判断。
- **业务评审意见**：【待填写】
- **新增正例**：【待填写】
- **新增反例**：【待填写】

## D08 `out_of_scope`｜范围外

- **评审结果**：[ ] 保留 [ ] 改名 [ ] 合并 [ ] 删除 [ ] 拆分
- **定义**：与兰花、商品购买、养护服务、订单和客户服务均无关的请求。
- **应当命中**：无关知识问答、写作、代码、娱乐等。
- **不应命中**：表达模糊但可能与当前对话相关；应优先使用上下文或澄清，而不是直接判定范围外。
- **初始正例**：
  1. “帮我写一段 Python 代码。”
  2. “给我讲个历史故事。”
  3. 【待补充真实正例】
- **初始反例**：
  1. “那这个呢？”（可能依赖上下文，应澄清或继承对象）
  2. “怎么处理？”（可能依赖上一轮问题）
  3. 【待补充易混淆反例】
- **易混淆标签**：`conversation` 以及信息不足但仍可能在业务范围内的消息
- **建议槽位**：`out_of_scope_topic`
- **销售信号/阶段影响**：通常保持阶段，不推进销售；连续范围外对话可由策略层决定退出。
- **业务评审意见**：【待填写】
- **新增正例**：【待填写】
- **新增反例**：【待填写】

---

# 5. Goal 标签评审卡

## G01 `express_need`｜表达需求

- **评审结果**：[ ] 保留 [ ] 改名 [ ] 合并 [ ] 删除 [ ] 拆分
- **定义**：用户主动表达想购买、解决或获得什么，但尚未必指向具体商品。
- **应当命中**：想要好养/香花/送礼的商品；希望解决养不活等问题。
- **不应命中**：只补充地区预算等事实；已经明确选择商品并要求下单。
- **初始正例**：“想找一盆适合新手的。”；“我想要开花香一点的。”；【待补充】
- **初始反例**：“我在广东。”（`provide_information`）；“就要这盆。”（`confirm_choice`）
- **易混淆标签**：`provide_information`, `seek_recommendation`, `purchase`
- **建议槽位**：`desired_outcome`, `purchase_purpose`, `product_requirements`
- **销售信号/阶段影响**：`product_need` 或 `service_need`；进入/保持挖需求。
- **业务评审意见及补充样例**：【待填写】

## G02 `provide_information`｜补充信息

- **评审结果**：[ ] 保留 [ ] 改名 [ ] 合并 [ ] 删除 [ ] 拆分
- **定义**：用户回答问题或主动提供地区、环境、预算、经验、偏好等可用于理解需求的事实。
- **应当命中**：我在北京；预算三百；阳台朝南；以前没养过。
- **不应命中**：用户在询问信息；用户只是说“好的”但没有实质内容。
- **初始正例**：“我在广东，放阳台养。”；“预算大概两百到三百。”；【待补充】
- **初始反例**：“广东适合养什么？”（`seek_recommendation`）；“好的。”（`affirm`）
- **易混淆标签**：`express_need`, `affirm`
- **建议槽位**：所有客户画像和需求槽位
- **销售信号/阶段影响**：`preference_revealed`, `pain_revealed`, `desired_outcome`；补齐条件后可推进阶段。
- **业务评审意见及补充样例**：【待填写】

## G03 `ask_information`｜询问信息

- **评审结果**：[ ] 保留 [ ] 改名 [ ] 合并 [ ] 删除 [ ] 拆分
- **定义**：用户询问事实、规则、参数、价格或方法，本身不必包含问题处理或购买动作。
- **应当命中**：是什么品种；多少钱；怎么养；有没有售后；多久发货。
- **不应命中**：已经出现具体异常并求解决；请求个性化推荐；明确要求购买。
- **初始正例**：“这是什么品种？”；“这个多少钱？”；【待补充】
- **初始反例**：“已经烂根了怎么办？”（`seek_solution`）；“给我推荐一款。”（`seek_recommendation`）
- **易混淆标签**：`seek_solution`, `seek_recommendation`, `query_status`
- **建议槽位**：由 Issue 决定
- **销售信号/阶段影响**：按 Issue 产生 `price_interest`、`service_need` 等；不能仅因询问就机械推进。
- **业务评审意见及补充样例**：【待填写】

## G04 `seek_recommendation`｜请求推荐

- **评审结果**：[ ] 保留 [ ] 改名 [ ] 合并 [ ] 删除 [ ] 拆分
- **定义**：用户希望系统根据需求推荐商品、品种或购买方案。
- **应当命中**：给新手推荐；广东阳台适合哪款；预算内选哪个。
- **不应命中**：用户只是询问某款商品事实；用户自己比较两个明确商品但未要求推荐结论。
- **初始正例**：“推荐一款新手好养的。”；“三百以内哪个适合送人？”；【待补充】
- **初始反例**：“这款多大？”（`ask_information`）；“这两个有什么区别？”（`compare_options`）
- **易混淆标签**：`express_need`, `compare_options`, `ask_information`
- **建议槽位**：`budget`, `region`, `placement`, `preferences`, `purchase_purpose`
- **销售信号/阶段影响**：`product_need`, `recommendation_ready`；信息完整后进入推品，否则继续挖需求。
- **业务评审意见及补充样例**：【待填写】

## G05 `compare_options`｜比较选择

- **评审结果**：[ ] 保留 [ ] 改名 [ ] 合并 [ ] 删除 [ ] 拆分
- **定义**：用户比较两个或多个商品、品种、规格或方案，希望了解差异或优劣。
- **应当命中**：哪款更好养；红色和黄色哪个好；A 与 B 有什么区别。
- **不应命中**：没有明确比较对象的推荐请求；只询问单个商品参数。
- **初始正例**：“这两款哪个更适合新手？”；“大苗和中苗有什么区别？”；【待补充】
- **初始反例**：“推荐一个好养的。”（`seek_recommendation`）；“这盆是什么品种？”（`ask_information`）
- **易混淆标签**：`seek_recommendation`, `ask_information`
- **建议槽位**：`comparison_product_ids`, `comparison_dimensions`
- **销售信号/阶段影响**：`recommendation_engaged`；通常处于推品或塑品。
- **业务评审意见及补充样例**：【待填写】

## G06 `seek_solution`｜寻求解决方案

- **评审结果**：[ ] 保留 [ ] 改名 [ ] 合并 [ ] 删除 [ ] 拆分
- **定义**：用户已经存在明确困难、异常或阻碍，希望系统告诉他如何解决。
- **应当命中**：烂根怎么办；一直不开花怎么处理；担心养不活应该选什么。
- **不应命中**：一般知识询问；真实订单售后要求退款或人工处理。
- **初始正例**：“已经烂根了怎么救？”；“以前总养死，该选什么样的？”；【待补充】
- **初始反例**：“多久浇一次水？”（`ask_information`）；“收到坏了，我要退款。”（`request_refund_return`）
- **易混淆标签**：`ask_information`, `seek_recommendation`, `request_service`
- **建议槽位**：`problem`, `symptom`, `desired_outcome`, `constraints`
- **销售信号/阶段影响**：`pain_revealed`, `service_need`；可支持找痛点或解决成交阻碍。
- **业务评审意见及补充样例**：【待填写】

## G07 `express_interest`｜表达兴趣或认可

- **评审结果**：[ ] 保留 [ ] 改名 [ ] 合并 [ ] 删除 [ ] 拆分
- **定义**：用户对已介绍商品或方案表达喜欢、认可或继续了解的兴趣，但尚未明确购买。
- **应当命中**：这个不错；挺喜欢这个颜色；感觉这款适合我。
- **不应命中**：单纯肯定系统收到；明确选择并购买；仅礼貌性回应。
- **初始正例**：“这款看起来不错。”；“这个颜色我挺喜欢。”；【待补充】
- **初始反例**：“好的，我知道了。”（`affirm`）；“就买这个。”（`purchase`）
- **易混淆标签**：`affirm`, `confirm_choice`, `purchase`
- **建议槽位**：`interested_product_id`, `liked_attributes`
- **销售信号/阶段影响**：`recommendation_engaged`, `value_acknowledged`；支持塑品或试成交。
- **业务评审意见及补充样例**：【待填写】

## G08 `express_objection`｜表达异议或顾虑

- **评审结果**：[ ] 保留 [ ] 改名 [ ] 合并 [ ] 删除 [ ] 拆分
- **定义**：用户表达阻碍购买的担心、质疑或不认可，但没有明确要求优惠或拒绝购买。
- **应当命中**：太贵；怕养不好；不确定真假；担心售后；感觉不值。
- **不应命中**：明确要求便宜；明确不买；单纯询问价格或保障政策。
- **初始正例**：“有点贵，我再想想。”；“挺喜欢，但我怕养死。”；【待补充】
- **初始反例**：“能便宜五十吗？”（`negotiate`）；“不用了，我不买。”（`decline_purchase`）
- **易混淆标签**：`negotiate`, `defer_decision`, `decline_purchase`, `ask_information`
- **建议槽位**：`decision_blockers`, `objection_type`, `objection_detail`
- **销售信号/阶段影响**：`objection`；根据是否已有具体商品决定继续需求确认或进入成交阻碍处理。
- **业务评审意见及补充样例**：【待填写】

## G09 `negotiate`｜议价或争取优惠

- **评审结果**：[ ] 保留 [ ] 改名 [ ] 合并 [ ] 删除 [ ] 拆分
- **定义**：用户明确希望降价、打折、赠送权益或获得包邮等优惠。
- **应当命中**：便宜点；最低多少；送点东西；能包邮吗。
- **不应命中**：只说贵；只问正常价格；询问现有活动但未提出额外要求时可视业务决定。
- **初始正例**：“能再便宜一点吗？”；“买两盆可以包邮吗？”；【待补充】
- **初始反例**：“这个多少钱？”（`ask_information`）；“太贵了。”（`express_objection`）
- **易混淆标签**：`ask_information`, `express_objection`
- **建议槽位**：`requested_price`, `requested_discount`, `requested_benefit`, `quantity`
- **销售信号/阶段影响**：`price_interest`, `objection`；通常处于试成交或成交推进。
- **业务评审意见及补充样例**：【待填写】

## G10 `confirm_choice`｜确认商品或方案

- **评审结果**：[ ] 保留 [ ] 改名 [ ] 合并 [ ] 删除 [ ] 拆分
- **定义**：用户明确锁定某个商品、规格或推荐方案，但未必已经提出下单动作。
- **应当命中**：就刚才那款；选红色中苗；还是要第二个方案。
- **不应命中**：只是表示喜欢；仍在比较；已明确要求购买数量和付款。
- **初始正例**：“就选刚才推荐的第二款。”；“确定要红色中苗。”；【待补充】
- **初始反例**：“第二款挺好看。”（`express_interest`）；“第二款和第三款哪个好？”（`compare_options`）
- **易混淆标签**：`express_interest`, `purchase`, `affirm`
- **建议槽位**：`selected_product_id`, `selected_sku_id`, `selected_attributes`
- **销售信号/阶段影响**：`selected_product`, `ready_to_buy` 的弱证据；支持试成交。
- **业务评审意见及补充样例**：【待填写】

## G11 `defer_decision`｜暂缓决定

- **评审结果**：[ ] 保留 [ ] 改名 [ ] 合并 [ ] 删除 [ ] 拆分
- **定义**：用户没有明确拒绝，只表示需要考虑、稍后决定或暂时不推进。
- **应当命中**：考虑一下；晚点回复；先看看；回头再说。
- **不应命中**：明确不买；因具体异议而犹豫但未表达暂缓时，主 Goal 可为 `express_objection`。
- **初始正例**：“我先考虑一下，晚点再说。”；“先看看，过两天决定。”；【待补充】
- **初始反例**：“不用了，我不买。”（`decline_purchase`）；“主要是太贵。”（`express_objection`）
- **易混淆标签**：`express_objection`, `decline_purchase`
- **建议槽位**：`follow_up_time`, `defer_reason`
- **销售信号/阶段影响**：保持商机，不强行推进；进入适度跟进策略。
- **业务评审意见及补充样例**：【待填写】

## G12 `decline_purchase`｜明确拒绝购买

- **评审结果**：[ ] 保留 [ ] 改名 [ ] 合并 [ ] 删除 [ ] 拆分
- **定义**：用户明确表示不购买、不要或终止当前购买决定。
- **应当命中**：不买了；不用了；这个我不要；决定不买。
- **不应命中**：只是暂时考虑；否认某个属性或纠正理解；取消已创建订单属于服务/交易操作。
- **初始正例**：“不用了，我不买了。”；“这个不考虑了。”；【待补充】
- **初始反例**：“我再考虑一下。”（`defer_decision`）；“不是红色的。”（`deny`）
- **易混淆标签**：`defer_decision`, `deny`, `end_conversation`
- **建议槽位**：`rejection_reason`, `rejected_product_id`
- **销售信号/阶段影响**：`purchase_rejected`；关闭当前机会或进入合规的流失挽回策略。
- **业务评审意见及补充样例**：【待填写】

## G13 `purchase`｜明确购买

- **评审结果**：[ ] 保留 [ ] 改名 [ ] 合并 [ ] 删除 [ ] 拆分
- **定义**：用户明确要求购买、下单或按指定数量获取商品。
- **应当命中**：我要这个；来两盆；帮我下单；现在买。
- **不应命中**：只确认商品但未表达购买；索要商品信息；询问价格。
- **初始正例**：“就要这个，给我来两盆。”；“可以，帮我下单吧。”；【待补充】
- **初始反例**：“我比较喜欢这个。”（`express_interest`）；“这个多少钱？”（`ask_information`）
- **易混淆标签**：`confirm_choice`, `pay`, `express_interest`
- **建议槽位**：`product_id`, `sku_id`, `quantity`, `buyer_requirements`
- **销售信号/阶段影响**：`ready_to_buy`；进入成交推进，但必须查询真实库存和价格。
- **业务评审意见及补充样例**：【待填写】

## G14 `pay`｜支付

- **评审结果**：[ ] 保留 [ ] 改名 [ ] 合并 [ ] 删除 [ ] 拆分
- **定义**：用户询问、准备执行或声称已经完成支付。
- **应当命中**：怎么付款；发付款码；我已经付了；支付失败。
- **不应命中**：只要求下单；询问价格；订单支付状态的事实查询可结合 `query_status`。
- **初始正例**：“微信怎么付款？”；“我已经付过了，查一下。”；【待补充】
- **初始反例**：“我要这个。”（`purchase`）；“总共多少钱？”（`ask_information`）
- **易混淆标签**：`purchase`, `query_status`, `ask_information`
- **建议槽位**：`payment_method`, `payment_claimed`, `transaction_id`, `payment_problem`
- **销售信号/阶段影响**：`payment_claimed`；只有业务系统确认后才能产生 `purchased_verified` 和成交关闭。
- **业务评审意见及补充样例**：【待填写】

## G15 `query_status`｜查询状态

- **评审结果**：[ ] 保留 [ ] 改名 [ ] 合并 [ ] 删除 [ ] 拆分
- **定义**：用户查询订单、支付、发货、物流、服务处理或退款的当前进度。
- **应当命中**：下单成功了吗；发货了吗；快递到哪；退款到账了吗。
- **不应命中**：购买前询问通常几天发货；请求执行退款；询问操作方法。
- **初始正例**：“我的订单发货了吗？”；“退款什么时候到账？”；【待补充】
- **初始反例**：“买了以后几天发货？”（`ask_information`）；“我要退款。”（`request_refund_return`）
- **易混淆标签**：`ask_information`, `request_service`, `request_refund_return`
- **建议槽位**：`order_id`, `tracking_no`, `status_type`
- **销售信号/阶段影响**：通常不推进销售；真实服务问题可触发中断。
- **业务评审意见及补充样例**：【待填写】

## G16 `request_service`｜请求客户服务

- **评审结果**：[ ] 保留 [ ] 改名 [ ] 合并 [ ] 删除 [ ] 拆分
- **定义**：用户针对已经发生的订单、配送、收货或使用问题，要求查询、解释或处理。
- **应当命中**：帮我查快递；收到有问题怎么处理；少发了一件；需要售后帮助。
- **不应命中**：购买前询问服务政策；纯养护知识咨询；明确退款退货或人工请求。
- **初始正例**：“收到的苗状态不好，帮我处理一下。”；“订单一直没发，帮我看看。”；【待补充】
- **初始反例**：“买了以后有人指导吗？”（售前 `ask_information`）；“我要退货。”（`request_refund_return`）
- **易混淆标签**：`seek_solution`, `query_status`, `request_refund_return`, `request_human`
- **建议槽位**：`order_id`, `service_problem`, `requested_resolution`
- **销售信号/阶段影响**：`after_sale_interruption`；暂停销售机会，处理完成后恢复。
- **业务评审意见及补充样例**：【待填写】

## G17 `request_refund_return`｜请求退款或退货

- **评审结果**：[ ] 保留 [ ] 改名 [ ] 合并 [ ] 删除 [ ] 拆分
- **定义**：用户明确要求退回款项或退回商品，第一版暂不拆分退款与退货流程。
- **应当命中**：我要退款；这个要退掉；帮我退钱；不想要了怎么退。
- **不应命中**：询问售前退款政策；查询退款进度；表达不购买但不存在订单。
- **初始正例**：“收到坏了，我要退款。”；“这个订单我想退掉。”；【待补充】
- **初始反例**：“坏了能退款吗？”（售前 `ask_information`）；“我暂时不买了。”（`decline_purchase`）
- **易混淆标签**：`ask_information`, `query_status`, `decline_purchase`, `request_service`
- **建议槽位**：`order_id`, `requested_resolution`, `refund_reason`, `return_reason`
- **销售信号/阶段影响**：高风险服务中断；由业务政策或人工处理，不由意图模块直接执行。
- **业务评审意见及补充样例**：【待填写】

## G18 `complain`｜投诉或强烈不满

- **评审结果**：[ ] 保留 [ ] 改名 [ ] 合并 [ ] 删除 [ ] 拆分
- **定义**：用户针对商品、服务或处理过程表达明确投诉、追责或强烈不满。
- **应当命中**：我要投诉；服务太差；一直没人处理；必须给说法。
- **不应命中**：普通价格异议；轻微负面评价但仍在正常选购；单纯描述商品问题。
- **初始正例**：“你们这个服务太差了，我要投诉。”；“处理这么久都没结果，必须给我说法。”；【待补充】
- **初始反例**：“这个有点贵。”（`express_objection`）；“收到的叶子有点黄。”（可能 `request_service`）
- **易混淆标签**：`express_objection`, `request_service`, `request_human`
- **建议槽位**：`complaint_target`, `complaint_reason`, `requested_resolution`
- **销售信号/阶段影响**：高风险服务/人工中断，不推进销售。
- **业务评审意见及补充样例**：【待填写】

## G19 `request_human`｜请求人工介入

- **评审结果**：[ ] 保留 [ ] 改名 [ ] 合并 [ ] 删除 [ ] 拆分
- **定义**：用户明确要求真人、人工客服、老板或售后人员接管当前对话。
- **应当命中**：转人工；找真人客服；让老板联系我；人工处理。
- **不应命中**：只是出现“客服”一词；询问是否有客服指导；要求系统提供服务但未要求真人。
- **初始正例**：“帮我转人工客服。”；“我想找真人处理。”；【待补充】
- **初始反例**：“有没有客服指导养护？”（`ask_information`）；“客服一般几点上班？”（是否转人工需结合语义）
- **易混淆标签**：`request_service`, `ask_information`
- **建议槽位**：`handoff_reason`, `preferred_contact_method`
- **销售信号/阶段影响**：`human_interruption`；暂停机会并记录恢复阶段。
- **业务评审意见及补充样例**：【待填写】

## G20 `affirm`｜肯定确认

- **评审结果**：[ ] 保留 [ ] 改名 [ ] 合并 [ ] 删除 [ ] 拆分
- **定义**：用户对上一轮问题、提议或事实作肯定回答，但没有更具体的业务动作。
- **应当命中**：是的；可以；好的；没问题。
- **不应命中**：确认具体商品选择；“好的”后继续提供实质信息；礼貌感谢。
- **初始正例**：“是的。”；“可以，就这样。”；【待补充】
- **初始反例**：“好的，我在广东阳台养。”（主 Goal `provide_information`）；“就要第二款。”（`confirm_choice`）
- **易混淆标签**：`confirm_choice`, `provide_information`, `social`
- **建议槽位**：`affirmed_proposition`
- **销售信号/阶段影响**：必须结合上一轮问题解释；不能仅凭“好的”认定购买。
- **业务评审意见及补充样例**：【待填写】

## G21 `deny`｜否认或纠正

- **评审结果**：[ ] 保留 [ ] 改名 [ ] 合并 [ ] 删除 [ ] 拆分
- **定义**：用户否定上一轮假设、纠正商品/需求信息，或说明系统理解错误。
- **应当命中**：不是；不对；我说的不是这个；不是红色。
- **不应命中**：明确不购买；表达商品异议；拒绝某个推荐但仍想继续选择时需结合上下文。
- **初始正例**：“不是，我是放室内养。”；“我说的不是这款。”；【待补充】
- **初始反例**：“我不买了。”（`decline_purchase`）；“这个品质不太行。”（`express_objection`）
- **易混淆标签**：`decline_purchase`, `express_objection`
- **建议槽位**：`denied_proposition`, `corrected_value`
- **销售信号/阶段影响**：修正会话状态；必要时回到需求确认，不直接关闭机会。
- **业务评审意见及补充样例**：【待填写】

## G22 `social`｜寒暄或感谢

- **评审结果**：[ ] 保留 [ ] 改名 [ ] 合并 [ ] 删除 [ ] 拆分
- **定义**：用户进行没有独立业务动作的问候、感谢或礼貌互动。
- **应当命中**：你好；在吗；谢谢；辛苦了。
- **不应命中**：寒暄后包含业务问题；表示对商品认可；明确结束对话。
- **初始正例**：“你好，在吗？”；“谢谢，辛苦了。”；【待补充】
- **初始反例**：“你好，这个多少钱？”（主 Goal `ask_information`）；“这个不错。”（`express_interest`）
- **易混淆标签**：`affirm`, `express_interest`, `end_conversation`
- **建议槽位**：通常为空
- **销售信号/阶段影响**：通常保持阶段。
- **业务评审意见及补充样例**：【待填写】

## G23 `end_conversation`｜结束会话

- **评审结果**：[ ] 保留 [ ] 改名 [ ] 合并 [ ] 删除 [ ] 拆分
- **定义**：用户明确表示当前问题已解决或希望结束对话，不等同于拒绝购买。
- **应当命中**：问题解决了；先这样；不用回复了；再见。
- **不应命中**：明确不购买；只说谢谢；暂缓决策但希望后续联系。
- **初始正例**：“问题解决了，谢谢，再见。”；“先这样吧，不用回复了。”；【待补充】
- **初始反例**：“我先考虑一下。”（`defer_decision`）；“这个我不买了。”（`decline_purchase`）
- **易混淆标签**：`social`, `defer_decision`, `decline_purchase`
- **建议槽位**：`end_reason`
- **销售信号/阶段影响**：结束当前会话，但商机是否关闭由策略层根据购买状态判断。
- **业务评审意见及补充样例**：【待填写】

## G24 `unclear`｜目标不清楚

- **评审结果**：[ ] 保留 [ ] 改名 [ ] 合并 [ ] 删除 [ ] 拆分
- **定义**：结合当前上下文后仍无法判断用户希望系统完成什么，需要澄清或补充信息。
- **应当命中**：无上下文的“那个呢”“怎么办”“不行”；多个候选动作都合理且无法区分。
- **不应命中**：可以从最近对话明确继承对象；虽然表达简短但业务动作明确。
- **初始正例**：“那个怎么弄？”（无可用上下文）；“还是不行。”（不知道指什么）；【待补充】
- **初始反例**：“那第二款呢？”（有明确商品上下文时可继续识别）；“我要人工。”（明确 `request_human`）
- **易混淆标签**：所有上下文依赖 Goal
- **建议槽位**：`missing_information`, `candidate_goals`
- **销售信号/阶段影响**：不推进阶段；生成一个最有区分力的澄清问题。
- **业务评审意见及补充样例**：【待填写】

---

# 6. Issue 标签评审卡

## I01 `purchase_purpose`｜购买用途

- **评审结果**：[ ] 保留 [ ] 改名 [ ] 合并 [ ] 删除 [ ] 拆分
- **定义**：用户购买商品的使用目的，如自养、送礼、收藏、办公室摆放。
- **正例**：“想买一盆送人。”；“办公室摆一盆。”；【待补充】
- **反例**：“我在阳台养。”（`growing_environment`）；“想要红色的。”（`preference`）
- **易混淆标签**：`preference`, `pain_and_outcome`
- **建议槽位**：`purchase_purpose`, `recipient`, `occasion`
- **销售价值/阶段**：用于挖需求和推荐适配。
- **业务评审意见及补充样例**：【待填写】

## I02 `experience_level`｜养兰经验

- **评审结果**：[ ] 保留 [ ] 改名 [ ] 合并 [ ] 删除 [ ] 拆分
- **定义**：用户自述的新手、已有经验或资深程度。
- **正例**：“我是第一次养兰花。”；“养了好几年了。”；【待补充】
- **反例**：“我总是养不活。”（也可能是 `pain_and_outcome`）；“这个适合新手吗？”（更偏 `recommendation_fit`）
- **易混淆标签**：`pain_and_outcome`, `recommendation_fit`
- **建议槽位**：`experience_level`, `years_of_experience`
- **销售价值/阶段**：决定推荐难度、服务深度和养护说明方式。
- **业务评审意见及补充样例**：【待填写】

## I03 `growing_environment`｜养护环境

- **评审结果**：[ ] 保留 [ ] 改名 [ ] 合并 [ ] 删除 [ ] 拆分
- **定义**：用户将要养护兰花的地区、空间、朝向、气候和环境条件。
- **正例**：“广东阳台朝南。”；“北方室内有暖气。”；【待补充】
- **反例**：“需要多少光照？”（`environment_care`）；“这个适合北方吗？”（`recommendation_fit`）
- **易混淆标签**：`environment_care`, `recommendation_fit`
- **建议槽位**：`region`, `city`, `placement`, `orientation`, `climate`, `has_heating`
- **销售价值/阶段**：重要推荐条件，支持挖需求到推品。
- **业务评审意见及补充样例**：【待填写】

## I04 `preference`｜商品偏好

- **评审结果**：[ ] 保留 [ ] 改名 [ ] 合并 [ ] 删除 [ ] 拆分
- **定义**：用户对颜色、香味、花型、大小、品种或风格的偏好。
- **正例**：“想要红色、香一点的。”；“喜欢小巧一点的。”；【待补充】
- **反例**：“预算三百。”（`budget`）；“想买来送人。”（`purchase_purpose`）
- **易混淆标签**：`product_information`, `purchase_purpose`
- **建议槽位**：`color_preference`, `fragrance_preference`, `size_preference`, `variety_preference`
- **销售价值/阶段**：形成推荐条件和商品筛选条件。
- **业务评审意见及补充样例**：【待填写】

## I05 `budget`｜预算

- **评审结果**：[ ] 保留 [ ] 改名 [ ] 合并 [ ] 删除 [ ] 拆分
- **定义**：用户可接受的购买金额区间或预算上限。
- **正例**：“预算两百左右。”；“最好不要超过五百。”；【待补充】
- **反例**：“这个卖多少钱？”（`price`）；“能便宜点吗？”（`discount`）
- **易混淆标签**：`price`, `discount`
- **建议槽位**：`budget_min`, `budget_max`, `currency`
- **销售价值/阶段**：推荐筛选条件；不能把预算低直接当作价格异议。
- **业务评审意见及补充样例**：【待填写】

## I06 `pain_and_outcome`｜困难与期望结果

- **评审结果**：[ ] 保留 [ ] 改名 [ ] 合并 [ ] 删除 [ ] 拆分
- **定义**：用户过去或当前的核心困难，以及希望通过商品或服务达到的结果。
- **正例**：“以前买的总养不活。”；“想找一个能稳定开花的。”；【待补充】
- **反例**：“叶子现在发黄怎么办？”（具体问题可用 `symptom_disease_recovery`）；“我喜欢红色。”（`preference`）
- **易混淆标签**：`experience_level`, `care_confidence`, `symptom_disease_recovery`
- **建议槽位**：`pain_point`, `desired_outcome`, `past_failure`
- **销售价值/阶段**：核心找痛点证据，也是推荐理由的重要来源。
- **业务评审意见及补充样例**：【待填写】

## I07 `product_information`｜商品信息

- **评审结果**：[ ] 保留 [ ] 改名 [ ] 合并 [ ] 删除 [ ] 拆分
- **定义**：商品的品种、花色、苗情、尺寸、规格、包装及其他客观属性。
- **正例**：“这是什么品种？”；“这盆苗有多大？”；【待补充】
- **反例**：“哪款适合新手？”（`recommendation_fit`）；“还有库存吗？”（`stock_availability`）
- **易混淆标签**：`recommendation_fit`, `product_comparison`, `stock_availability`
- **建议槽位**：`product_id`, `variety`, `color`, `seedling_size`, `specification`
- **销售价值/阶段**：支持推品和塑品；事实必须来自商品库。
- **业务评审意见及补充样例**：【待填写】

## I08 `recommendation_fit`｜推荐与适配

- **评审结果**：[ ] 保留 [ ] 改名 [ ] 合并 [ ] 删除 [ ] 拆分
- **定义**：商品是否适合用户的经验、环境、预算、用途和偏好，以及基于这些条件的推荐。
- **正例**：“这款适合新手吗？”；“广东阳台推荐哪一种？”；【待补充】
- **反例**：“这盆多大？”（`product_information`）；“这两款有什么区别？”（`product_comparison`）
- **易混淆标签**：`product_information`, `product_comparison`, `growing_environment`
- **建议槽位**：`fit_dimensions`, `recommended_product_ids`, `fit_reason`
- **销售价值/阶段**：推荐核心 Issue；支持从需求确认进入推品。
- **业务评审意见及补充样例**：【待填写】

## I09 `product_comparison`｜商品比较

- **评审结果**：[ ] 保留 [ ] 改名 [ ] 合并 [ ] 删除 [ ] 拆分
- **定义**：两个或多个商品、品种、规格或方案之间的差异、优势和适用性比较。
- **正例**：“这两款哪个更香？”；“中苗和大苗差别是什么？”；【待补充】
- **反例**：“推荐一个香的。”（`recommendation_fit`）；“这款是什么品种？”（`product_information`）
- **易混淆标签**：`recommendation_fit`, `product_information`, `product_value`
- **建议槽位**：`comparison_product_ids`, `comparison_dimensions`
- **销售价值/阶段**：说明客户已参与推荐，通常支持推品或塑品。
- **业务评审意见及补充样例**：【待填写】

## I10 `stock_availability`｜库存与可选规格

- **评审结果**：[ ] 保留 [ ] 改名 [ ] 合并 [ ] 删除 [ ] 拆分
- **定义**：商品、颜色、规格或数量当前是否可售。
- **正例**：“红色的还有货吗？”；“大苗现在能买几盆？”；【待补充】
- **反例**：“红色的多少钱？”（`price`）；“我想要红色的。”（`preference` 或 `sku_quantity`）
- **易混淆标签**：`product_information`, `sku_quantity`, `preference`
- **建议槽位**：`product_id`, `sku_id`, `requested_quantity`, `stock_status`
- **销售价值/阶段**：成交前事实校验；不能由模型猜测库存。
- **业务评审意见及补充样例**：【待填写】

## I11 `care_general`｜综合养护

- **评审结果**：[ ] 保留 [ ] 改名 [ ] 合并 [ ] 删除 [ ] 拆分
- **定义**：没有聚焦具体操作或症状的综合性养护问题，例如怎么养、是否好养。
- **正例**：“这个平时怎么养？”；“这款好不好养？”；【待补充】
- **反例**：“多久浇一次水？”（`watering_fertilizing`）；“叶子黄了怎么办？”（`symptom_disease_recovery`）
- **易混淆标签**：其他养护 Issue、`care_confidence`
- **建议槽位**：`product_id`, `variety`, `experience_level`
- **销售价值/阶段**：既可提供服务，也可能揭示购买前养护顾虑。
- **业务评审意见及补充样例**：【待填写】

## I12 `watering_fertilizing`｜水肥管理

- **评审结果**：[ ] 保留 [ ] 改名 [ ] 合并 [ ] 删除 [ ] 拆分
- **定义**：浇水、施肥、水质、肥料类型、浓度和频率相关问题。
- **正例**：“几天浇一次水？”；“什么时候可以施肥？”；【待补充】
- **反例**：“冬天放哪里？”（`environment_care`）；“用什么植料？”（`medium_repotting`）
- **易混淆标签**：`environment_care`, `medium_repotting`, `care_general`
- **建议槽位**：`watering_frequency`, `water_type`, `fertilizer_type`, `fertilizing_frequency`
- **销售价值/阶段**：服务型 Issue；购买前出现时可帮助建立养护信心。
- **业务评审意见及补充样例**：【待填写】

## I13 `environment_care`｜环境管理

- **评审结果**：[ ] 保留 [ ] 改名 [ ] 合并 [ ] 删除 [ ] 拆分
- **定义**：养护方法中的光照、温度、湿度、通风、遮阴和季节环境管理。
- **正例**：“需要晒太阳吗？”；“北方冬天温度怎么控制？”；【待补充】
- **反例**：“我在北京室内养。”（`growing_environment`）；“这款适合北京吗？”（`recommendation_fit`）
- **易混淆标签**：`growing_environment`, `recommendation_fit`, `care_general`
- **建议槽位**：`light`, `temperature`, `humidity`, `ventilation`, `season`
- **销售价值/阶段**：解决适配和养护顾虑；必要时反馈到商品推荐。
- **业务评审意见及补充样例**：【待填写】

## I14 `medium_repotting`｜植料与换盆

- **评审结果**：[ ] 保留 [ ] 改名 [ ] 合并 [ ] 删除 [ ] 拆分
- **定义**：植料、盆器、排水、换盆、修根和上盆等操作。
- **正例**：“用什么植料比较好？”；“收到后要不要马上换盆？”；【待补充】
- **反例**：“根已经烂了怎么救？”（`symptom_disease_recovery`）；“放阳台合适吗？”（`environment_care`）
- **易混淆标签**：`symptom_disease_recovery`, `care_general`
- **建议槽位**：`medium_type`, `pot_type`, `repot_timing`, `root_action`
- **销售价值/阶段**：服务型 Issue；可与商品套餐或养护指导关联，但不能强推附加商品。
- **业务评审意见及补充样例**：【待填写】

## I15 `symptom_disease_recovery`｜异常、病害与恢复

- **评审结果**：[ ] 保留 [ ] 改名 [ ] 合并 [ ] 删除 [ ] 拆分
- **定义**：黄叶、烂根、干尖、萎蔫、不开花、病虫害、抢救和服盆恢复等具体问题。
- **正例**：“叶子一直发黄怎么办？”；“烂根了还能救吗？”；【待补充】
- **反例**：“这个平时怎么养？”（`care_general`）；“收到时叶子已经折了。”（`received_problem`）
- **易混淆标签**：`care_general`, `received_problem`, `medium_repotting`
- **建议槽位**：`symptom`, `affected_part`, `duration`, `suspected_disease`, `recovery_stage`
- **销售价值/阶段**：服务和痛点证据；购买前可影响推荐，购买后提供持续服务。
- **业务评审意见及补充样例**：【待填写】

## I16 `price`｜价格

- **评审结果**：[ ] 保留 [ ] 改名 [ ] 合并 [ ] 删除 [ ] 拆分
- **定义**：商品正常售价、报价、价格高低和总金额。
- **正例**：“这盆多少钱？”；“两盆一共什么价格？”；【待补充】
- **反例**：“预算不超过三百。”（`budget`）；“能便宜点吗？”（`discount`）
- **易混淆标签**：`budget`, `discount`, `product_value`
- **建议槽位**：`product_id`, `sku_id`, `quoted_price`, `quantity`
- **销售价值/阶段**：产生 `price_interest`；商品未明确时不能直接推进试成交，价格必须来自可信商品事实。
- **业务评审意见及补充样例**：【待填写】

## I17 `discount`｜优惠

- **评审结果**：[ ] 保留 [ ] 改名 [ ] 合并 [ ] 删除 [ ] 拆分
- **定义**：折扣、优惠券、满减、赠品、议价、包邮等优惠权益。
- **正例**：“有优惠吗？”；“买两盆能不能包邮？”；【待补充】
- **反例**：“这个多少钱？”（`price`）；“为什么这么贵？”（可能 `product_value`）
- **易混淆标签**：`price`, `product_value`, `delivery`
- **建议槽位**：`promotion_id`, `requested_discount`, `requested_benefit`, `quantity`
- **销售价值/阶段**：通常处于试成交或成交推进；政策事实不可编造。
- **业务评审意见及补充样例**：【待填写】

## I18 `product_value`｜商品价值

- **评审结果**：[ ] 保留 [ ] 改名 [ ] 合并 [ ] 删除 [ ] 拆分
- **定义**：客户对商品为什么值得购买、价格依据、品质优势和方案价值的关注或认可。
- **正例**：“为什么这款比普通的贵？”；“这款主要好在哪里？”；【待补充】
- **反例**：“现在卖多少钱？”（`price`）；“能便宜五十吗？”（`discount`）
- **易混淆标签**：`price`, `quality_trust`, `product_comparison`
- **建议槽位**：`value_dimensions`, `selected_product_id`
- **销售价值/阶段**：塑品核心 Issue；只能基于可验证商品价值，不使用虚假稀缺或销量。
- **业务评审意见及补充样例**：【待填写】

## I19 `quality_trust`｜品质与信任

- **评审结果**：[ ] 保留 [ ] 改名 [ ] 合并 [ ] 删除 [ ] 拆分
- **定义**：客户对苗情、品种真实性、商家可信度、对版和描述真实性的关注。
- **正例**：“能保证品种对版吗？”；“你们家的苗情靠谱吗？”；【待补充】
- **反例**：“收到的苗已经断了。”（`received_problem`）；“这款有什么优势？”（`product_value`）
- **易混淆标签**：`product_value`, `service_guarantee`, `received_problem`
- **建议槽位**：`trust_concern`, `quality_dimension`, `evidence_requested`
- **销售价值/阶段**：重要成交阻碍；应使用真实商品信息、图片和保障政策建立信任。
- **业务评审意见及补充样例**：【待填写】

## I20 `care_confidence`｜养护信心

- **评审结果**：[ ] 保留 [ ] 改名 [ ] 合并 [ ] 删除 [ ] 拆分
- **定义**：用户因为担心不会养、养不好或养死而产生的购买顾虑。
- **正例**：“我怕买回去养死。”；“新手能不能养得住？”；【待补充】
- **反例**：“多久浇一次水？”（`watering_fertilizing`）；“以前养死过好几盆。”（也可能 `pain_and_outcome`）
- **易混淆标签**：`care_general`, `recommendation_fit`, `pain_and_outcome`
- **建议槽位**：`care_concern`, `past_failure`, `support_expected`
- **销售价值/阶段**：形成 `care_concern` 决策阻碍；先解决顾虑再推进成交。
- **业务评审意见及补充样例**：【待填写】

## I21 `service_guarantee`｜服务与保障

- **评审结果**：[ ] 保留 [ ] 改名 [ ] 合并 [ ] 删除 [ ] 拆分
- **定义**：购买前对养护指导、对版、到货保障、服务承诺和责任边界的关注。
- **正例**：“买了以后有人指导吗？”；“运输坏了有保障吗？”；【待补充】
- **反例**：“收到已经坏了，帮我处理。”（`received_problem`）；“售后申请期限是多久？”（可按语境判 `after_sale_policy`）
- **易混淆标签**：`quality_trust`, `after_sale_policy`, `received_problem`
- **建议槽位**：`guarantee_topic`, `support_type`, `policy_requested`
- **销售价值/阶段**：购买保障和信任阻碍；售前出现时不应暂停销售流程。
- **业务评审意见及补充样例**：【待填写】

## I22 `sku_quantity`｜规格与数量

- **评审结果**：[ ] 保留 [ ] 改名 [ ] 合并 [ ] 删除 [ ] 拆分
- **定义**：成交阶段对具体商品规格、颜色、苗型和购买数量的选择或确认。
- **正例**：“红色中苗要两盆。”；“大苗给我来一盆。”；【待补充】
- **反例**：“红色的还有货吗？”（`stock_availability`）；“喜欢红色的。”（`preference`）
- **易混淆标签**：`stock_availability`, `preference`, `product_information`
- **建议槽位**：`product_id`, `sku_id`, `color`, `specification`, `quantity`
- **销售价值/阶段**：成交必要槽位；需要库存和价格事实校验。
- **业务评审意见及补充样例**：【待填写】

## I23 `order_process`｜下单流程

- **评审结果**：[ ] 保留 [ ] 改名 [ ] 合并 [ ] 删除 [ ] 拆分
- **定义**：如何购买、创建订单、获取购买入口、填写收货信息和确认订单。
- **正例**：“怎么买，发个链接。”；“帮我创建订单。”；【待补充】
- **反例**：“微信怎么付款？”（`payment`）；“订单发货了吗？”（`delivery`/状态查询）
- **易混淆标签**：`payment`, `delivery`
- **建议槽位**：`purchase_link_requested`, `recipient`, `phone`, `address`, `order_id`
- **销售价值/阶段**：成交推进；敏感收货信息需要按隐私规则处理。
- **业务评审意见及补充样例**：【待填写】

## I24 `payment`｜支付

- **评审结果**：[ ] 保留 [ ] 改名 [ ] 合并 [ ] 删除 [ ] 拆分
- **定义**：支付方式、付款入口、支付异常、支付声明和支付状态。
- **正例**：“微信怎么付？”；“我付了但订单没显示。”；【待补充】
- **反例**：“怎么下单？”（`order_process`）；“总价多少？”（`price`）
- **易混淆标签**：`order_process`, `price`, `delivery`
- **建议槽位**：`payment_method`, `payment_status`, `transaction_id`, `payment_problem`
- **销售价值/阶段**：成交推进；支付完成必须通过业务系统验证。
- **业务评审意见及补充样例**：【待填写】

## I25 `delivery`｜配送服务

- **评审结果**：[ ] 保留 [ ] 改名 [ ] 合并 [ ] 删除 [ ] 拆分
- **定义**：统一承载运费、包邮、发货时间、预计到货、物流查询和配送异常，第一版不继续细分。
- **正例**：“运费多少？”；“我的快递到哪里了？”；【待补充】
- **反例**：“收到时苗已经断了。”（`received_problem`）；“能便宜一点吗？”（通常 `discount`）
- **易混淆标签**：`discount`, `received_problem`, `order_process`
- **建议槽位**：`delivery_topic`（shipping_fee/dispatch_time/tracking/delivery_time/exception）, `order_id`, `tracking_no`
- **销售价值/阶段**：售前是购买条件，不中断销售；订单后真实异常进入客户服务处理。
- **业务评审意见及补充样例**：【待填写】

## I26 `after_sale_policy`｜售后政策

- **评审结果**：[ ] 保留 [ ] 改名 [ ] 合并 [ ] 删除 [ ] 拆分
- **定义**：售后范围、申请方式、有效期限、责任条件和处理规则。
- **正例**：“售后期限是多久？”；“需要什么凭证才能申请售后？”；【待补充】
- **反例**：“买回去养死包赔吗？”（购买前更偏 `service_guarantee`）；“收到坏了，我要退款。”（`received_problem` + `refund_return`）
- **易混淆标签**：`service_guarantee`, `received_problem`, `refund_return`
- **建议槽位**：`policy_topic`, `policy_condition`, `order_id`
- **销售价值/阶段**：售前可作为保障说明；真实售后流程根据订单事实处理。
- **业务评审意见及补充样例**：【待填写】

## I27 `received_problem`｜收货问题

- **评审结果**：[ ] 保留 [ ] 改名 [ ] 合并 [ ] 删除 [ ] 拆分
- **定义**：商品到货后的损坏、状态差、错发、少发、货不对板等事实性问题。
- **正例**：“收到时叶子和根都断了。”；“我买两盆只收到一盆。”；【待补充】
- **反例**：“运输坏了有保障吗？”（售前 `service_guarantee`）；“自己养了一周后黄叶了。”（可能 `symptom_disease_recovery`）
- **易混淆标签**：`service_guarantee`, `symptom_disease_recovery`, `refund_return`
- **建议槽位**：`order_id`, `problem_type`, `affected_quantity`, `received_at`, `evidence_available`
- **销售价值/阶段**：真实客户服务中断；先核实订单、时间和证据，再决定处理方式。
- **业务评审意见及补充样例**：【待填写】

## I28 `refund_return`｜退款退货

- **评审结果**：[ ] 保留 [ ] 改名 [ ] 合并 [ ] 删除 [ ] 拆分
- **定义**：退款、退货及其原因、申请和进度，第一版暂不细分多个售后动作。
- **正例**：“这个订单我要退款。”；“商品怎么退回去？”；【待补充】
- **反例**：“坏了可以退款吗？”（售前政策/保障咨询）；“我不买了。”（不存在订单时为 `decline_purchase`）
- **易混淆标签**：`after_sale_policy`, `received_problem`, `decline_purchase`
- **建议槽位**：`order_id`, `resolution_type`, `reason`, `refund_status`, `return_tracking_no`
- **销售价值/阶段**：高风险服务中断；只识别诉求，不直接执行退款或退货。
- **业务评审意见及补充样例**：【待填写】

---

# 7. 建议输出结构

```json
{
  "primary_domain": "commercial_decision",
  "secondary_domains": ["care_service"],
  "primary_goal": "express_objection",
  "secondary_goals": ["express_interest"],
  "issues": ["price", "care_confidence"],
  "scope": "in_scope",
  "evidence": [
    {
      "text": "有点贵",
      "supports": "express_objection/price"
    },
    {
      "text": "怕养不好",
      "supports": "care_confidence"
    }
  ],
  "slots": {
    "decision_blockers": ["price", "care_confidence"]
  }
}
```

# 8. 总体评审结论

- **Domain 是否需要调整**：【待填写】
- **Goal 是否需要调整**：【待填写】
- **Issue 是否需要调整**：【待填写】
- **建议删除的标签**：【待填写】
- **建议合并的标签**：【待填写】
- **建议新增的标签**：【待填写】
- **需要进一步明确的业务边界**：【待填写】
- **评审人**：【待填写】
- **评审日期**：【待填写】
- **下一版版本号**：【待填写】

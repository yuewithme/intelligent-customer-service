# 11 实施状态与运维交接

## 交付状态

Sales Agent Harness V2 已完成本地全量开发并直接替换原主决策架构。当前代码没有影子模式、灰度比例或旧链路回退开关；回退使用 Git。此次交付没有连接云服务器，也没有执行生产部署。

替换前基线：`pre-agent-harness-v2`（提交 `8046f8fa`，标签已推送远端）。

## 当前运行链

```text
微信 / API 输入
  -> 入站去重、合并与技术噪声过滤
  -> 客户工作区
  -> 单一 Sales Agent 循环
       -> Sales Core + 小兰风格
       -> capability.search 渐进披露工具与销售经验
       -> 自主查询、准备卡片、记录记忆、创建唤醒或转人工
  -> 服务端事实与权限硬校验
  -> 逻辑回复包
  -> 微信风控出站队列
  -> sent / failed / cancelled 状态回写
```

主实现位于：

- `apps/api/app/domains/decisioning/services/agent_prompt.py`
- `apps/api/app/domains/decisioning/services/agent_runtime.py`
- `apps/api/app/domains/decisioning/services/agent_tools.py`
- `apps/api/app/domains/conversations/services/chat_orchestrator.py`
- `apps/api/app/domains/sales/services/daily_touch_service.py`
- `apps/api/app/integrations/eyun/services/message_risk_control_service.py`

## 已退出运行时的旧能力

- 固定意图分类、意图样本向量库和意图路由。
- 销售阶段判断、固定动作节点、ReplyPlan 与策略优先级链。
- 固定未购买 SOP、固定开场话术、固定资料关键词与资料回复。新好友开场只保留“自我介绍 + 品牌图片 + 单个挖需问题”的结构，前后文字由 Agent 生成。
- 固定话术库、场景题库、匹配日志及对应管理端入口。
- 意图影子、回复影子和会话案例影子运行。
- 旧 Persona 渲染与回复图；人格评测脚本已改为直接调用新 Agent。

为避免破坏性迁移，历史数据库中已经存在的影子、话术库等旧表不会主动删除，新运行链也不再映射或调用它们。少量会话兼容字段仍可写入中性的 `autonomous_sales_turn` 观察值，但不参与路由。若要清理生产历史表或兼容列，应另行备份并执行显式数据库迁移，不能把清理与业务切换混在一起。

## Agent 自主权与系统硬边界

Agent 自主决定每轮商业判断、关系目的、表达方式、工具组合、资料时机、商品卡片时机和专项唤醒。系统只硬控以下内容：

- 商品、价格、库存、订单、物流、权益、资料引用和优惠结果必须来自真实工具。
- `prepared`、`queued` 不得表述为 `sent`；未被 Agent 放入最终回复包的预备卡片不会发送。
- 退款、赔偿、改价、改订单、客户要求人工等授权动作必须转人工。
- 客户隔离、真实引用、参数校验、近期重复资料、防内部状态泄露和未核实促销话术。
- 客户可见表达每轮最多一个问题；编号、项目符号、Markdown 和不必要引号会触发 Agent 自行重写，不用固定话术替换。
- 微信队列顺序、依赖、限速、重试、状态回写和技术不可发送状态。

普通表情、短句、“嗯”“好的”等不按垃圾信息丢弃，会交给 Agent 结合上下文理解；只过滤明确的传输占位、系统事件和无法形成客户语义的 UI 噪声。

默认销售思路已经调整为关系经营优先：新客户先理解来意、围绕当前话题聊天、提供一次真实帮助，并从服务中自然塑造萧岚苑团队价值；新好友身份、普通兰花话题和养护咨询本身不触发商品工具。客户出现真实选购或购买信号后，Agent 立即查询真实商品并推进。这是核心提示和渐进披露经验，不是新增阶段机、关键词路由或必须走完的固定节点。

## 每日触达

- 技术上可发送且当天没有真实 `sent` 的客户，会生成持久化每日唤醒。
- 当天普通会话回复与人工消息均计入；同一逻辑回复包的多条消息只算一次触达。
- 普通客户每天最多两次主动触达；明确文字拒绝后降低为每天一次，但不永久放弃。
- 人工会话中不抢发，由系统提醒当前负责人完成今日触达。
- 唤醒任务带幂等键、处理租约、最多三次尝试和 15 分钟重试；发送前若客户产生新消息，会取消陈旧内容并重新判断。

## 配置交接

新 Agent 使用通用业务模型配置：

```env
BUSINESS_LLM_PROVIDER=dashscope
BUSINESS_LLM_MODEL=qwen3.7-plus
```

每日触达主要配置：

```env
DAILY_TOUCH_ENABLED=true
DAILY_TOUCH_POLL_SECONDS=60
DAILY_TOUCH_TIMEZONE=Asia/Shanghai
DAILY_TOUCH_WINDOW_START=08:00
DAILY_TOUCH_WINDOW_END=23:00
DAILY_TOUCH_BATCH_SIZE=20
```

旧的 `INTENT_*`、`REPLY_SHADOW_*`、`TALK_SCRIPT_LLM_*`、固定开场文字和 Qdrant 意图 / 话术集合配置已从配置契约及示例环境文件移除。开场品牌图片使用 `EYUN_OPENING_IMAGE_URL` 或 `EYUN_OPENING_MATERIAL_ID` 配置。旧部署环境中即使暂时保留已退出的键，也会因 Settings 的额外字段忽略策略而不参与运行。

## 验证结果

2026-08-05 本地最终验证：

- 后端：`python -m pytest -q`，444 项通过，0 项失败；仅有 Starlette 关于测试客户端依赖的弃用警告。
- 管理端：TypeScript 类型检查通过。
- 管理端：生产构建通过。
- 专项覆盖：Agent 工具循环、关系经营优先与成熟购买直达、真实卡片、未核实价格 / 库存 / 发送状态拦截、单问题自然表达、新好友开场结构与专用队列、资料资产、订单状态、输入过滤、微信队列、每日触达幂等 / 计数 / 重试 / 租约恢复和人工接管。

## Git 回退

优先使用可逆提交：

1. 找到本次 Harness V2 提交。
2. 在 `main` 上执行 `git revert <提交号>` 并正常推送。
3. 重新部署回退后的 `main`。

若需要完整恢复替换前版本，可从标签 `pre-agent-harness-v2` 构建部署。不要 force-push，不要对生产工作树执行 `reset --hard`。回退代码不会自动删除新 Agent 创建的表；这些新增表对旧版本无调用影响。

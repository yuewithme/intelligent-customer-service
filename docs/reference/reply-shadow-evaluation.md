# 影子销售决策评测

完整历史会话的案例库与逐轮影子回放见
[`conversation-case-shadow-evaluation.md`](conversation-case-shadow-evaluation.md)。

影子销售决策在正式回复完成后异步运行候选策略。正式回复照常发送并更新客户状态；
候选结果只写入 `reply_shadow_runs`，不会发送消息、更新客户状态、写入客户记忆或调用
订单等可写工具。

## 信息流

```text
真实消息
  ├─ 生产链路 -> ReplyPlan -> 正式回复 -> 正式状态更新
  └─ 只读快照 -> 影子候选 -> 自动问题标记 -> 人工审核 -> 评测集导出
```

生产版和候选版使用相同的 `trace_id`。只读快照包含当前消息、最近对话、必要客户状态、
意图、正式 ReplyPlan、已验证业务事实和正式回复；敏感字段、手机号、电话和邮箱在进入
候选模型和落库前会被脱敏。

## 配置

影子能力默认关闭：

```env
REPLY_SHADOW_ENABLED=false
REPLY_SHADOW_SAMPLE_PERCENT=10
REPLY_SHADOW_HIGH_RISK_ALWAYS=true
REPLY_SHADOW_LLM_PROVIDER=dashscope
REPLY_SHADOW_LLM_MODEL=qwen3.7-plus
REPLY_SHADOW_EXPERIMENT_ID=followup_decision_v1
REPLY_SHADOW_CANDIDATE_VERSION=followup_v1
REPLY_SHADOW_PROMPT_VERSION=v1
```

启用后，普通请求按稳定哈希抽样；价格、优惠、订单、支付、退款、投诉、物流、人工和
明确拒绝等高风险消息默认全部抽样。修改候选逻辑时必须更新 experiment、candidate 或
prompt 版本，避免不同实验结果混在一起。

## 审核接口

- `GET /api/v1/admin/reply-shadows`：分页查询，支持状态、审核状态、优先级和关键词过滤。
- `GET /api/v1/admin/reply-shadows/{trace_id}`：查看相同输入下的生产/影子完整对比。
- `POST /api/v1/admin/reply-shadows/{trace_id}/annotations`：提交盲评结果。
- `GET /api/v1/admin/reply-shadows/export`：导出已确认的 JSONL 评测数据。

审核 verdict：

- `primary_better`
- `shadow_better`
- `tie`
- `both_bad`
- `uncertain`
- `excluded`

只有 `primary_better`、`shadow_better` 和 `tie` 会进入当前导出集；`both_bad` 不会被误当
成黄金答案。

## 使用顺序

1. 本地或测试环境开启影子，确认零外部副作用。
2. 生产环境从低比例普通流量开始，高风险案例进入人工审核。
3. 审核生产/影子的阶段、路由、销售动作、回复和跟进建议。
4. 将确认样本导出为版本化评测集，执行离线回归。
5. 只有安全门槛、人工盲评和成本门槛全部通过，才进入小流量真实 A/B；影子结果本身
   不能证明成交率提升。

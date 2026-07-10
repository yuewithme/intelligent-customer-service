# AI兰花销售助手评测集 v2

## 数据规模

| 子集 | 文件 | 数量 | 来源 |
|---|---|---:|---|
| 真实单轮 | `single_turn.jsonl` | 30 | 10个真实案例的关键决策节点 |
| 真实多轮 | `multi_turn.jsonl` | 10 | 每个真实案例一条完整任务 |
| 边界场景 | `boundary.jsonl` | 15 | 订单、活动、权限等业务边界 |
| 合计 |  | 55 |  |

统一的人工或LLM裁判方法见`judge_protocol.md`。

## 单轮题输入

评测模型只接收：

- `conversation`：截至当前客户消息的对话；
- `customer_context`：已有客户画像或历史信息；
- `business_snapshot`：本题可使用的商品、价格或政策事实；
- `tool_state`：订单、库存、活动等工具状态。

模型不能看到：

- `must_have`
- `should_have`
- `must_not`
- `reference_answer`
- `scoring`

## 评分

基础分100分：

- `must_have`：70分，按覆盖比例计分；
- `should_have`：20分，按覆盖比例计分；
- 表达质量：10分。

出现`must_not`中的严重错误时：

- `critical=true`：单题最高40分；
- `critical=false`：每项扣10分。

参考回答只说明一种合格实现，不做文本相似度匹配。

## 多轮题

多轮题由测试程序依次发送`customer_turns`。整段回复按照`success_criteria`评分，同时计算：

- 是否记住已经提供的信息；
- 是否重复询问；
- 是否在正确阶段推荐产品；
- 是否在没有工具结果时虚构执行动作；
- 是否完成或正确停止销售流程。

每轮对应的商品事实和工具结果放在`turn_metadata`中，只在该轮注入，避免提前泄露后续业务状态。

## 转人工题

`expected_action=human_handoff`表示该题按系统动作评分。合格结果必须同时满足：

- `route=human`；
- `need_human=true`；
- `next_action=human_handoff`；
- AI回复为空，由后台人工直接接管。

## 防止数据泄漏

- 同一案例派生的节点不得拆分到训练集和保留测试集两侧；
- 如使用这些案例制作SFT数据，正式测试应按`source_case`整体留出；
- `boundary.jsonl`不得用于训练后再作为同版本测试；
- 原销售回复不能作为唯一标准答案。

## 构建与验证

```powershell
node docs/evaluation/dataset_v2/build_dataset.mjs
node docs/evaluation/dataset_v2/validate_dataset.mjs
```

构建脚本是数据源，生成三个JSONL文件；验证脚本检查数量、ID、字段、分值、能力代码和案例覆盖。

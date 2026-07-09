# AI兰花销售助手评测集 v1

## 数据规模

| 子集 | 文件 | 数量 | 来源 |
|---|---|---:|---|
| 真实单轮 | `single_turn.jsonl` | 30 | 10个真实案例的关键决策节点 |
| 真实多轮 | `multi_turn.jsonl` | 10 | 每个真实案例一条完整任务 |
| 派生边界 | `boundary.jsonl` | 15 | 基于真实场景改变一个业务条件 |
| 合计 |  | 55 |  |

`source_type=real`表示题目来自案例事实；`source_type=derived`表示为测试边界而构造，不得当作真实聊天。

统一的人工或LLM裁判方法见`judge_protocol.md`。

## 单轮题输入

评测模型只接收：

- `conversation`：截至当前客户消息的对话；
- `knowledge_context`：本题允许使用的专业或商品知识；
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

多轮题由测试程序依次发送`customer_turns`。每轮回复按照对应`checkpoints`评分，同时计算：

- 是否记住已经提供的信息；
- 是否重复询问；
- 是否在正确阶段推荐产品；
- 是否在没有工具结果时虚构执行动作；
- 是否完成或正确停止销售流程。

## 防止数据泄漏

- 同一案例派生的节点不得拆分到训练集和保留测试集两侧；
- 如使用这些案例制作SFT数据，正式测试应按`source_case`整体留出；
- `boundary.jsonl`不得用于训练后再作为同版本测试；
- 原销售回复不能作为唯一标准答案。

## 构建与验证

```powershell
node docs/evaluation/dataset_v1/build_dataset.mjs
node docs/evaluation/dataset_v1/validate_dataset.mjs
```

构建脚本是数据源，生成三个JSONL文件；验证脚本检查数量、ID、字段、分值、能力代码、来源类型和数据隔离字段。

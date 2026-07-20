# 首单七阶段专项评测

本目录覆盖七阶段正常推进、强信号跳级、受控回环、信息不足、六类成交阻碍、可信付款边界、售后/人工中断和事实安全。

运行：

```powershell
cd wechat_rag_bot
python -m evaluation.run_evaluation --dataset-dir ../docs/evaluation/first_order_sales_flow --output-dir ../evaluation_results/first_order_sales_flow
```

`summary.json` 除通用裁判分数外，还记录 `stage_accuracy`、`action_accuracy`、`slot_repeat_question_rate`、`fact_hallucination_rate`、`wrong_deal_rate` 和 `human_handoff_accuracy`。上线门槛：错误成交、事实幻觉、重复多问均为 0，人工测试账号完成一轮七阶段验收后再扩大灰度。

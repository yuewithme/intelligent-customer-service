# 固定触达重复发送修复

## 任务目标

修复每日固定触达在亿云已受理、但自身消息确认回调缺失时被重复补发的问题，并完善安全重试、回调确认、发送幂等和运营可观测性。

## 实施计划

1. 停止对“已受理但未确认”的消息自动补发，超时后改为待核验。
2. 区分明确失败、结果未知、已受理和已确认，只对可安全重试的明确临时失败自动重试。
3. 保存并匹配亿云返回与回调中的多个消息 ID，补充媒体回调确认信息。
4. 为固定触达的文案和素材增加稳定发送键与数据库幂等约束，并在运营接口展示待核验及发送轨迹。

每个阶段完成最小相关验证后，分别提交并推送 GitLab `main`。

## 完成结果

- 阶段一：确认超时消息改为 `unconfirmed`，不再自动补发或标记为发送失败；消息后台显示“待人工核验”，并允许人工决定是否补发。
- 阶段二：网络超时、连接中断等不确定结果改为 `delivery_unknown`，明确拒绝改为 `failed`，两者均不再盲目自动重发；组合消息可继续处理后续素材。
- 阶段三：发送响应和自身回调中的 `newMsgId`、`msgId` 均被保存并参与确认匹配，匹配同时限定微信实例；确认事件记录消息类型，覆盖图片和视频回调诊断。
- 阶段四：固定触达文案和素材使用唯一 `delivery_key`，重复入队直接复用原记录；消息后台展示发送尝试次数和亿云消息 ID，运营统计接口返回最近发送轨迹；对结果未知或待核验消息执行人工补发前明确提示重复风险并要求二次确认。
- 四个阶段均已完成；前三阶段已分别推送，第四阶段随本次最终结果提交推送。

## 验证情况

- 阶段一：`pytest apps/api/tests/test_message_risk_control.py -k "stale_accepted_outbound or accepted_outbound_is_confirmed" -q` 通过（1 passed），管理端 `pnpm ts:check` 通过。
- 阶段二：发送结果分类测试通过（3 passed），管理端 `pnpm ts:check` 通过。
- 阶段三：发送 ID 别名和自身媒体回调测试通过（2 passed + 1 passed）。
- 阶段四及跨阶段回归：`pytest apps/api/tests/test_message_risk_control.py apps/api/tests/test_eyun_callback.py apps/api/tests/test_service_material_touch_service.py -q` 通过（75 passed），旧补发队列迁移与发送键测试追加验证通过（2 passed）；管理端 `pnpm ts:check` 通过。

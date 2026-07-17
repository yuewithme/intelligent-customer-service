# Eyun 微信出站网关

所有发往微信的业务消息都必须调用
`app.services.message_risk_control_service.enqueue_wechat_outbound` 加入持久化队列。

禁止业务模块直接调用 `send_eyun_text`、`send_eyun_image`、`send_eyun_video`、
`send_eyun_received_media` 或 `send_eyun_mini_program`。这些函数只能由风控 worker
在取得账号发送时间槽后调用。

新模块接入时至少提供：

- `w_id`：Eyun 微信实例。
- `wc_id`：微信接收方。
- `content` 和 `message_type`：消息内容与类型。
- `source_batch_key`：可追溯的业务来源标识。
- 组合消息应使用 `depends_on_outbound_id` 保证顺序。
- 如果管理台需要展示状态，传入 `conversation_message_id`。

队列按 `w_id` 串行发送，默认上限为每分钟 30 条，发送间隔在不低于滑动窗口
安全值的前提下加入随机抖动。即使短时间加入数百条消息，也只会增加队列积压，
不会并发直调 Eyun 发送接口。

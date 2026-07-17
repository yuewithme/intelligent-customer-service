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

## 批量发送相同图片或视频

批量媒体不允许每个收件人重复上传同一文件。先把素材发送到
`EYUN_MATERIAL_GROUP_WC_ID` 配置的微信素材群，系统从 Eyun 回调中对原始 XML
做 SHA-256 去重并存入素材库。也可以在工作台中将一条已收到的图片或视频
手动“存为微信素材”。

业务模块仅传 `material_id`，不复制 XML；通用批量入口为
`POST /api/v1/admin/wechat-materials/bulk-send`。入口会在一个事务中将所有收件人和
组合消息展开为持久化队列，同一收件人内部使用依赖链保序。worker 实际发送时
才根据 `material_id` 取出 XML，并调用 Eyun 的接收素材转发接口。

如 Eyun 返回 CDN/素材失效错误，素材会转为 `expired`，相关队列消息停在
`waiting_material`，不会降级为重复上传。重新捕获到同一 XML 后，系统会恢复这些队列消息。

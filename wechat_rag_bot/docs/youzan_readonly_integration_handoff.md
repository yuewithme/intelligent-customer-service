# 有赞云只读查询接入技术交接文档

更新日期：2026-07-18

本文档用于把当前智能客服接入有赞云的目标、接口、数据流、配置、已实现代码、已知问题和验收标准完整交接给后续开发者或其他 AI。无需回看原始聊天即可继续开发和排查。

## 1. 最终目标

智能客服只开放两类有赞能力：

1. 商品查询：根据客户聊天中的商品名称或关键词查询有赞在售商品，回复商品名称、价格等基本信息，并通过易云向客户发送有赞微信小程序商品卡片，客户自行进入小程序下单。
2. 订单查询：从当前或近期聊天中取得客户主动提供的下单手机号，查询该客户近期订单，回复商品摘要、订单状态和物流基本信息，并发送有赞小程序“我的订单”卡片供客户自行查看详情。

明确不做：

- 不退款。
- 不取消订单。
- 不修改收货地址。
- 不修改商品、价格或库存。
- 不代替客户下单。
- 不让大模型直接拼接 SQL 或直接访问数据库。
- 不建设复杂的独立身份认证系统。

最小安全边界：手机号只能来自客户当前消息、当前会话已保存状态或近期客户消息；不允许按模糊姓名批量查订单，不向客户返回完整地址等无关字段，不在日志中记录 Token、应用密钥或完整手机号。

## 2. 总体架构

```text
微信客户
  ↓
易云消息回调
  ↓
聊天编排 / 意图识别
  ├─ product_query
  └─ order_query
  ↓
Commerce Query Service
  ├─ YouzanProductService
  └─ YouzanOrderService
  ↓
YouzanClient
  ↓
有赞云 Open API

查询结果
  ↓
确定性回复渲染
  ↓
文本消息 + 易云 /sendApplets 小程序卡片
```

大模型只负责理解客户表达，不负责生成 API 地址、鉴权参数或执行写操作。具体调用由确定性代码完成。

## 3. 商品查询链路

### 3.1 触发场景

典型客户表达：

```text
有没有建兰皇帝？
把秋水伊人的购买链接发我
这款兰花怎么买？
发我商品链接
```

意图名称：

```text
product_query
```

如果客户只说“发我链接”而当前消息里没有商品名，应从近期客户消息中提取商品关键词；仍无法确定时，追问客户想看哪个品种或规格，不调用有赞。

### 3.2 有赞接口

```text
POST https://open.youzanyun.com/api/youzan.items.onsale.get/3.0.0
Query: access_token=<TOKEN>
Content-Type: application/json
```

请求体：

```json
{
  "q": "商品关键词",
  "page_no": 1,
  "page_size": 3
}
```

当前接口配置：

```text
method  = youzan.items.onsale.get
version = 3.0.0
```

关注返回字段：

```text
data.count
data.items[].item_id
data.items[].title
data.items[].alias
data.items[].price       单位：分
data.items[].quantity
data.items[].image / pic_url
data.items[].detail_url
data.items[].page_url
```

内部标准化结构：

```json
{
  "item_id": "123456",
  "title": "商品名称",
  "alias": "商品别名",
  "price_cent": 5880,
  "stock": 25,
  "image_url": "https://...",
  "page_path": "小程序商品页路径",
  "h5_url": "https://..."
}
```

### 3.3 回复规则

查询成功：

```text
给您找到这款“商品名称”，当前售价58.8元，点击商品卡片就可以查看和下单。
```

查不到：

```text
暂时没有查到合适的商品，您可以再告诉我品种、颜色或规格。
```

有赞失败：

```text
当前有赞系统暂时无法查询，请稍后再试。
```

禁止把 Token 错误、IP 白名单错误、权限错误错误地回复成“没有商品”。

## 4. 订单查询链路

### 4.1 触发场景

典型客户表达：

```text
帮我查一下订单
我的订单发货了吗？
快递到哪里了？
查询一下近期订单
```

意图名称：

```text
order_query
```

### 4.2 手机号取得顺序

按以下优先级使用手机号：

1. 当前客户消息里的中国大陆手机号。
2. `user_state.metadata.commerce_mobile` 中当前会话已保存的手机号。
3. 近期聊天中 `role=customer/user` 的客户消息。

不得从助手消息中提取手机号，避免把客服示例号码当成客户手机号。

缺少手机号时：

```text
可以的，请把下单手机号发给我，我帮您查询一下。
```

同时保存：

```json
{
  "commerce_pending": "order_mobile"
}
```

客户下一条只发送手机号时，意图仍应保持为 `order_query`。成功取得手机号后保存 `commerce_mobile` 并清除 `commerce_pending`。

### 4.3 第一步：手机号查询有赞客户

不要直接把手机号传给订单列表接口。当前设计是先查有赞客户标识：

```text
POST https://open.youzanyun.com/api/youzan.scrm.customer.get/3.0.0
Query: access_token=<TOKEN>
Content-Type: application/json
```

请求体：

```json
{
  "mobile": "客户主动提供的手机号"
}
```

从真实响应中取得：

```text
yz_uid
```

兼容字段：

```text
buyer_id
```

注意：真实店铺返回结构必须在云服务器容器内实测，不能只依赖 Mock 测试。如果 `yz_uid` 位于嵌套对象，应修正字段映射并补回归测试。

### 4.4 第二步：客户标识查询订单

```text
POST https://open.youzanyun.com/api/youzan.trades.sold.get/4.0.0
Query: access_token=<TOKEN>
Content-Type: application/json
```

请求体：

```json
{
  "buyer_id": "上一步取得的 yz_uid",
  "page_no": 1,
  "page_size": 3
}
```

目标是返回最近 3 个订单，不做全量同步。

当前代码兼容的列表字段：

```text
full_order_info_list
trades
items
orders
```

当前代码兼容的订单嵌套字段：

```text
full_order_info
order_info
orders
delivery_order
express_info
logistics
```

内部标准化结构：

```json
{
  "order_no": "E202607130001",
  "created_at": "2026-07-13 10:30:00",
  "status": "WAIT_BUYER_CONFIRM_GOODS",
  "status_text": "已发货",
  "item_summary": "商品名称 × 1",
  "express_company": "顺丰",
  "tracking_no_masked": "SF12******90"
}
```

订单状态映射：

```text
WAIT_BUYER_PAY             → 待付款
WAIT_SELLER_SEND_GOODS     → 待发货
WAIT_BUYER_CONFIRM_GOODS   → 已发货
TRADE_BUYER_SIGNED         → 已完成
TRADE_CLOSED               → 已关闭
```

### 4.5 回复规则

查询成功：

```text
查到您近期的订单：
1. 商品名称 × 1，已发货，顺丰
详细信息可以点击订单卡片查看。
```

查不到：

```text
暂时没有查到近期订单，请核对下单手机号，或点击订单卡片自行查看。
```

当前最小方案不展示完整收货地址，不执行任何订单写操作。

## 5. 有赞鉴权

自用型无容器应用使用应用 `client_id`、`client_secret` 和店铺 `kdt_id` 换取 Token：

```text
POST https://open.youzanyun.com/auth/token
Content-Type: application/json
```

```json
{
  "authorize_type": "silent",
  "client_id": "<CLIENT_ID>",
  "client_secret": "<CLIENT_SECRET>",
  "grant_id": "<KDT_ID>"
}
```

运行时使用：

```text
data.access_token
```

注意：

- `grant_id` 是店铺 `kdt_id`，不是应用 ID。
- 每个店铺有自己的 Token。
- 当前项目使用静态 `YOUZAN_ACCESS_TOKEN`，尚未实现自动换取/刷新。
- `client_secret` 和 `access_token` 不得写入 Git、前端、聊天记录或普通日志。

## 6. 有赞 IP 白名单

有赞校验的是调用 API 时的公网出口 IP，不是管理后台的公网入口 IP或 Docker 映射端口。

必须从云服务器的生产 API 容器内发起真实有赞请求，以有赞返回的来源 IP 为准。不能使用本机 Docker Desktop、内网穿透入口或普通 `api.ipify.org` 结果代替真实验证。

白名单错误示例：

```json
{
  "gw_err_resp": {
    "err_code": 4007,
    "err_msg": "源IP地址...非法调用有赞云，请完成IP地址确认后，前往应用中心控制台进行IP白名单配置",
    "trace_id": "..."
  }
}
```

有赞控制台路径：

```text
应用中心 → 应用概况 → 安全配置 → IP白名单
```

白名单只填公网 IP，不填端口。

## 7. API 错误处理要求

这是当前实现最需要修复的部分。

客户端必须按以下顺序判断：

1. HTTP 状态码是否为 2xx。
2. 响应是否为合法 JSON 对象。
3. 是否存在 `gw_err_resp`。
4. 是否存在 `error_response`。
5. `success` 是否为 `false`。
6. `code` 是否存在且不等于 `200`。
7. 只有以上均正常时，才读取 `data`。

当前 `YouzanClient` 只判断 `success=false` 或顶层非 200 `code`，没有识别 `gw_err_resp`。因此 IP 白名单错误可能被包装成普通字典，商品服务随后得到空列表，最终错误回复“没有商品”。

正确行为：

```text
gw_err_resp/error_response/非200
→ 抛出 YouzanError
→ Commerce Query Service 转换为 status=unavailable
→ 客服回复“当前有赞系统暂时无法查询，请稍后再试”
```

日志可以记录：

```text
接口方法
错误码
trace_id
异常类型
```

日志不得记录：

```text
access_token
client_secret
完整手机号
完整订单隐私数据
```

## 8. 微信小程序卡片

易云接口：

```text
POST <EYUN_BASE_URL>/sendApplets
Authorization: <EYUN_AUTHORIZATION>
Content-Type: application/json
```

请求体：

```json
{
  "wId": "易云微信实例ID",
  "wcId": "客户微信ID",
  "displayName": "小程序名称",
  "iconUrl": "https://稳定公网地址/icon.jpg",
  "appId": "wx...",
  "pagePath": "小程序页面路径",
  "thumbUrl": "https://稳定公网地址/thumb.jpg",
  "title": "卡片标题",
  "userName": "gh_...@app"
}
```

易云成功码：

```text
code = "1000"
```

`iconUrl` 和 `thumbUrl` 使用稳定公网 PNG/JPG，建议小于 50KB。`appId`、`userName` 和真实 `pagePath` 最可靠的来源是已收到的有赞小程序消息回调。

### 8.1 商品卡片路径

从有赞后台取得真实商品小程序推广路径：

```text
商品 → 商品管理 → 推广商品 → 微信小程序 → 复制推广路径
```

配置模板支持：

```text
{alias}
{item_id}
{kdt_id}
```

示意：

```dotenv
YOUZAN_PRODUCT_PAGE_PATH_TEMPLATE=真实路径中的商品别名替换为{alias}
```

不要凭示例猜测店铺的商品路径。

### 8.2 订单卡片路径

有赞曾提供“我的订单”通用小程序路径：

```text
packages/trade/order/list/index
```

仍应在当前正式小程序中点击验证；若店铺版本不同，以真实小程序回调中的 `pagePath` 为准。

## 9. 环境变量

生产环境配置文件：

```text
deploy/env/backend.prod.env
```

需要配置：

```dotenv
YOUZAN_ENABLED=true
YOUZAN_BASE_URL=https://open.youzanyun.com
YOUZAN_ACCESS_TOKEN=<真实Token>
YOUZAN_KDT_ID=<真实kdt_id>

YOUZAN_PRODUCT_SEARCH_METHOD=youzan.items.onsale.get
YOUZAN_PRODUCT_SEARCH_VERSION=3.0.0
YOUZAN_CUSTOMER_GET_METHOD=youzan.scrm.customer.get
YOUZAN_CUSTOMER_GET_VERSION=3.0.0
YOUZAN_ORDER_SEARCH_METHOD=youzan.trades.sold.get
YOUZAN_ORDER_SEARCH_VERSION=4.0.0

YOUZAN_PRODUCT_PAGE_PATH_TEMPLATE=<真实商品页模板>
YOUZAN_PRODUCT_H5_URL_TEMPLATE=
YOUZAN_MINI_PROGRAM_APP_ID=<wx...>
YOUZAN_MINI_PROGRAM_USER_NAME=<gh_...@app>
YOUZAN_MINI_PROGRAM_DISPLAY_NAME=<小程序名称>
YOUZAN_MINI_PROGRAM_ICON_URL=<稳定公网图片>
YOUZAN_ORDER_PAGE_PATH=<真实订单页路径>
YOUZAN_ORDER_CARD_TITLE=查看我的订单
YOUZAN_ORDER_CARD_THUMB_URL=<稳定公网图片>
```

禁止把真实值写入 `.env.example`、README 或 Git。

## 10. 当前代码位置

```text
app/integrations/youzan/client.py
    有赞 HTTP 客户端

app/services/youzan_product_service.py
    商品查询和字段标准化

app/services/youzan_order_service.py
    手机号 → yz_uid → 订单列表

app/services/commerce_query_service.py
    对话状态、手机号提取、商品/订单工具编排

app/services/intent_service.py
    product_query / order_query 识别及手机号续问

app/services/business_reply_renderer.py
    确定性商品/订单回复和小程序卡片生成

app/services/eyun_callback_service.py
    /sendApplets 调用

app/services/message_risk_control_service.py
    文本和小程序卡片出站队列
```

相关测试：

```text
tests/test_youzan_services.py
tests/test_commerce_query_flow.py
tests/test_eyun_mini_program.py
```

## 11. 当前已知状态与缺口

已完成：

- 商品与订单只读服务的代码结构。
- 商品和订单对话意图。
- 缺手机号追问和手机号续问状态。
- 商品与订单确定性回复。
- 易云小程序卡片发送和消息队列编码。
- Mock 单元测试。

仍需完成或实测：

1. 从真正的云服务器 Docker API 容器验证商品接口；不能用本机 `desktop-linux` Docker context 代替。
2. 修复 `YouzanClient` 对 `gw_err_resp` 和 `error_response` 的识别，并增加回归测试。
3. 使用一个经过授权的测试手机号验证 `youzan.scrm.customer.get` 的真实响应结构。
4. 验证 `youzan.trades.sold.get 4.0.0` 的真实订单、商品和物流字段映射。
5. 填写并验证小程序 `appId`、`userName`、商品页路径、订单页路径和图片地址。
6. 实现或安排 Token 自动重新获取，避免静态 Token 过期后查询中断。
7. 做微信端完整验收：客户提问 → 有赞查询 → 文本 → 小程序卡片 → 点击正确页面。

## 12. 验收用例

### A. 商品正常查询

输入：

```text
把建兰皇帝的购买链接发我
```

预期：

- 有赞返回匹配商品。
- 回复商品名称和元单位价格。
- 易云先发文本，再发商品小程序卡片。
- 点击卡片进入该商品，而不是店铺首页或其他商品。

### B. 商品无结果

预期：提示客户补充品种、颜色或规格，不生成虚假商品。

### C. 商品上游失败

模拟 `gw_err_resp=4007`、Token 过期、权限不足。

预期：回复系统暂时无法查询，不能回复“没有商品”。

### D. 订单缺手机号

输入：

```text
帮我查订单
```

预期：追问下单手机号，并设置 `commerce_pending=order_mobile`。

### E. 手机号续问

客户下一条只发手机号。

预期：保持 `order_query`，执行手机号 → `yz_uid` → 订单列表流程。

### F. 订单正常查询

预期：最多返回 3 个近期订单摘要；物流单号脱敏；发送“我的订单”卡片。

### G. 订单不存在

预期：提示核对手机号或点击订单卡片自行查看，不暴露其他客户信息。

### H. 部署与网络

预期：

- 测试命令从云服务器生产 API 容器内执行。
- 有赞返回顶层 `success=true`、`code=200`。
- 云服务器固定出口 IP 已加入有赞白名单。
- 重启 Docker 后仍能查询。

## 13. 建议后续 AI 的执行顺序

```text
P0 连接并确认真正的云服务器 Docker 环境
P0 从云端容器执行原始商品 API 健康检查
P0 修复 gw_err_resp/error_response 错误识别并补测试
P0 使用测试手机号验证客户和订单真实返回结构
P1 配置并验证商品/订单小程序卡片
P1 完成微信端端到端测试
P1 增加 Token 自动获取或过期恢复
P2 增加监控：错误码、trace_id、接口延迟和连续失败告警
```

后续 AI 不应要求用户在聊天中粘贴 `client_secret` 或 `access_token`。应在服务器环境变量中读取，并只输出“是否已设置”、长度、错误码和 `trace_id` 等脱敏诊断信息。

## 14. 官方资料

- 有赞商品接口场景说明：<https://developers.youzanyun.com/article/1567497554527>
- 有赞自用型应用数据打通流程：<https://help.youzan.com/displaylist/detail_13_13-2-52098>
- 有赞 `access_token` 说明：<https://help.youzan.com/displaylist/detail_4_4-1-20865>
- 有赞小程序推广路径说明：<https://help.youzan.com/displaylist/detail_4_4-1-21727>
- 有赞“我的订单”路径说明：<https://help.youzan.com/displaylist/detail_5_5-1-26279>
- 易云发送小程序：<https://wkteam.cn/docs/api-wen-dang2/xiao-xi-fa-song/sendApplets.html>

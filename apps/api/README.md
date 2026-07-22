# WeChat RAG Bot

一个以统一聊天入口为核心的 FastAPI 客服服务。当前已从单链路 RAG 升级为 Intent Router + Template Engine + Knowledge RAG + State Machine 的第一阶段框架；微信仍然只是接入层，不能绕过统一主流程。

## 架构

```text
微信 / 企业微信 / 小程序 / 第三方后端
                  ↓
            Channel Adapter
                  ↓
             State Manager
                  ↓
              Rule Guard
                  ↓
            Intent Router
                  ↓
      Policy / Tag / Sales Evidence
                  ↓
            Reply Planner
                  ↓
              ReplyPlan
                  ↓
 LangGraph: Template / Business / RAG / Clarify / Human
                  ↓
             State Update
```

回复决策只有一条权威链路：意图、规则、标签和销售阶段服务只产出证据，`reply_planner` 是唯一的优先级解析器，`ReplyPlan` 是内部执行契约，LangGraph 是唯一回复执行器。业务快照和工具状态只作为 `BusinessFacts` 输入，必须经过业务渲染器转换为客户语言，不能直接拼入答案。精简决策轨迹只写入管理日志，不向客户返回完整计划或工具状态。

## 本地启动

要求 Python 3.11+。

```bash
cd apps/api
python -m venv .venv
# Windows
.venv\Scripts\activate
python -m pip install -r requirements.txt
copy .env.example .env
uvicorn app.main:app --reload
```

服务地址为 `http://127.0.0.1:8000`，OpenAPI 文档位于 `/docs`，健康检查位于 `/health`。

若只想离线验证流程，可在 `.env` 使用：

```dotenv
API_AUTH_ENABLED=false
QDRANT_URL=
EMBEDDING_PROVIDER=mock
LLM_PROVIDER=mock
```

此模式使用进程内向量存储、确定性 mock embedding 和 mock LLM，不需要云端密钥。进程重启后内存知识会清空。

第一阶段默认还包含：

```dotenv
INTENT_LLM_PROVIDER=mock
STATE_PROVIDER=memory
RULE_GUARD_ENABLED=true
DEBUG_API_ENABLED=true
```

其中模板库、意图样本和用户状态都是 mock/内存实现。后续可把模板和意图样本接入 Qdrant，把状态接入 Redis/PostgreSQL。

AI 模型按用途读取配置。`LLM_PROVIDER` / `LLM_MODEL` 是通用默认值；用途级配置为空时自动回退到通用默认值：

```dotenv
LLM_PROVIDER=volcengine
LLM_MODEL=doubao-seed-1-6-flash-250615
LLM_TIMEOUT_SECONDS=180

RAG_LLM_PROVIDER=
RAG_LLM_MODEL=
BUSINESS_LLM_PROVIDER=
BUSINESS_LLM_MODEL=
INTENT_LLM_PROVIDER=
INTENT_LLM_MODEL=
TALK_SCRIPT_LLM_PROVIDER=
TALK_SCRIPT_LLM_MODEL=
PROFILE_LLM_PROVIDER=
PROFILE_LLM_MODEL=
REVIEW_LLM_PROVIDER=
REVIEW_LLM_MODEL=
PROFILE_ANALYSIS_PROMPT=
```

复杂回复链路允许单次模型请求最多等待 180 秒；评测客户端默认等待 240 秒，为 API 编排和网络传输预留空间。

用户画像生成使用 `PROFILE_LLM_PROVIDER` / `PROFILE_LLM_MODEL`，输入只包含用户消息原文记录；`PROFILE_ANALYSIS_PROMPT` 为空时使用内置画像提示词。

固定销售话术库已移除，不再参与回复生成。回复计划现在直接进入业务事实模板、知识检索、闲聊或转人工分支。

转人工通知逻辑当前只保留 `human_handoff_service` 接口，暂不真正推送给指定人员。

## Docker 启动

```bash
copy .env.example .env
docker compose up --build
```

## 认证

除微信回调外，业务 API 使用：

```http
Authorization: Bearer change_me
```

生产环境必须修改 `API_KEY`。设置 `API_AUTH_ENABLED=false` 可关闭认证，仅建议本地开发使用。

## 统一聊天接口

`POST /api/v1/chat`

```bash
curl -X POST http://127.0.0.1:8000/api/v1/chat \
  -H "Authorization: Bearer change_me" \
  -H "Content-Type: application/json" \
  -d '{
    "channel": "api",
    "user_id": "user_001",
    "session_id": null,
    "message": "员工报销需要谁审批？",
    "kb_id": "kb_default",
    "metadata": {
      "tenant_id": "tenant_default",
      "permission": "public"
    }
  }'
```

成功响应始终保留 `code`、`message`、`data`：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "answer": "员工报销需要主管审批。",
    "session_id": "sess_xxx",
    "sources": [],
    "usage": {
      "prompt_tokens": 0,
      "completion_tokens": 0
    },
    "reply_type": "template",
    "route": "template_reply",
    "intent": {},
    "template": {},
    "need_human": false,
    "next_action": null,
    "trace_id": "req_xxx",
    "metadata": {},
    "handoff": null
  }
}
```

`answer`、`session_id`、`sources`、`usage` 是旧字段，保持兼容；后面的字段是新增路由与调试信息。

## 意图识别 1.0 与 MVP 兜底

意图识别模块只输出结构化 `IntentResult`，不直接生成回复，也不直接调用模板、RAG 或人工系统。当前版本规则优先，意图样本可辅助增强置信度，LLM JSON 分类仅预留在低置信场景，默认关闭。

意图层仍可产出以下 route 作为决策证据，最终执行动作由 `reply_planner` 统一解析：

- `human`：人工、退款、投诉、强烈不满，直接转人工。
- `template_reply`：价格、优惠、物流、售后、下单、付款，进入模板流程。
- `rag_answer`：知识、流程、方法、资料、说明类问题，进入知识库 RAG。
- `template_then_rag`：兼容的混合意图证据；规划器会规范化为单一 `rag_answer` 执行动作，并在 `original_route` 中保留来源。
- `chitchat`：你好、在吗、谢谢等寒暄。
- `clarify`：表达不清或规则低置信。
- `unsupported`：明显业务外或不支持的问题。

明确人工、退款和高风险投诉会进入人工接管。转人工响应 `answer=""`，`reply_type="human"`，`route="human"`，`need_human=true`，`next_action="human_handoff"`，并在 `metadata.handoff` 中返回 `ticket_id`、`status`、`reason`；`clarify`、`unsupported`、RAG 无答案和模板未命中分别使用图内对应的安全兜底，不再维护第二套旧执行器。

后续可把 `clarify` 改为追问，把 `unsupported` 改为业务兜底，把 RAG 无答案改为相似推荐，并接入真实 LLM 意图 JSON 分类。

## 模板与意图接口

### 创建模板

`POST /api/v1/templates`

```bash
curl -X POST http://127.0.0.1:8000/api/v1/templates \
  -H "Authorization: Bearer change_me" \
  -H "Content-Type: application/json" \
  -d '{
    "template_id": "tpl_price_objection_001",
    "intent": "price_objection",
    "stage": "objection_handling",
    "trigger_examples": ["有点贵", "太贵了"],
    "content": "我理解你会关注价格。这个价格主要包含品质筛选、养护支持和售后保障。",
    "priority": 90
  }'
```

### 搜索模板

`POST /api/v1/templates/search`

```bash
curl -X POST http://127.0.0.1:8000/api/v1/templates/search \
  -H "Authorization: Bearer change_me" \
  -H "Content-Type: application/json" \
  -d '{
    "message": "有点贵，我再考虑一下",
    "intent": "price_objection",
    "stage": "objection_handling",
    "customer_tags": ["price_sensitive"],
    "top_k": 5
  }'
```

### 创建意图样本

`POST /api/v1/intent-examples`

```bash
curl -X POST http://127.0.0.1:8000/api/v1/intent-examples \
  -H "Authorization: Bearer change_me" \
  -H "Content-Type: application/json" \
  -d '{
    "example_id": "ex_price_001",
    "text": "有点贵，我再考虑一下",
    "route": "template_reply",
    "primary_intent": "price_objection",
    "secondary_intents": ["hesitation"],
    "sales_stage": "objection_handling"
  }'
```

### 调试意图识别

`POST /api/v1/debug/intent`

```bash
curl -X POST http://127.0.0.1:8000/api/v1/debug/intent \
  -H "Authorization: Bearer change_me" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "user_001",
    "message": "这个有点贵，而且我怕养不活",
    "session_id": "sess_xxx",
    "kb_id": "kb_default"
  }'
```

## 用户状态接口

`GET /api/v1/users/{user_id}/state`

```bash
curl -X GET http://127.0.0.1:8000/api/v1/users/user_001/state \
  -H "Authorization: Bearer change_me"
```

`PATCH /api/v1/users/{user_id}/state`

```bash
curl -X PATCH http://127.0.0.1:8000/api/v1/users/user_001/state \
  -H "Authorization: Bearer change_me" \
  -H "Content-Type: application/json" \
  -d '{
    "sales_stage": "objection_handling",
    "customer_tags": ["price_sensitive"],
    "risk_level": "normal"
  }'
```

## User Profile APIs

The legacy `GET/PATCH /api/v1/users/{user_id}/state` APIs are retained. The
profile APIs below provide the formal user profile, conversation memory, and
profile event access path. All profile APIs require:

```http
Authorization: Bearer change_me
```

`GET /api/v1/users/{user_id}/profile`

```bash
curl -X GET http://127.0.0.1:8000/api/v1/users/user_001/profile \
  -H "Authorization: Bearer change_me"
```

`PATCH /api/v1/users/{user_id}/profile`

```bash
curl -X PATCH http://127.0.0.1:8000/api/v1/users/user_001/profile \
  -H "Authorization: Bearer change_me" \
  -H "Content-Type: application/json" \
  -d '{
    "current_stage": "objection_handling",
    "risk_level": "normal",
    "customer_tags": ["vip", "price_sensitive"],
    "product_interests": ["orchid"],
    "ai_summary": "New orchid buyer, price-sensitive.",
    "preference_summary": "Prefers easy-care products.",
    "pain_points": ["price_sensitive", "care_difficulty"],
    "is_human_handoff": false,
    "human_ticket_id": null,
    "human_handoff_status": null,
    "human_handoff_reason": null,
    "metadata": {
      "reason": "operator_update"
    }
  }'
```

`GET /api/v1/users/{user_id}/memories`

```bash
curl -X GET "http://127.0.0.1:8000/api/v1/users/user_001/memories?limit=10" \
  -H "Authorization: Bearer change_me"
```

`GET /api/v1/users/{user_id}/profile/events`

```bash
curl -X GET "http://127.0.0.1:8000/api/v1/users/user_001/profile/events?limit=20" \
  -H "Authorization: Bearer change_me"
```

Profile data is written from three paths: `/api/v1/chat` appends user and
assistant memories and updates the profile; manual profile PATCH writes a
`profile_patched` event; human-route replies mark handoff fields and write a
`handoff_created` event. Memory query results are returned oldest-to-newest
within the requested recent window so clients can render the conversation
directly.

## 文档入库

`POST /api/v1/knowledge/upload` 支持 `txt`、`md`、`pdf`：

```bash
curl -X POST http://127.0.0.1:8000/api/v1/knowledge/upload \
  -H "Authorization: Bearer change_me" \
  -F "file=@员工手册.pdf" \
  -F "kb_id=kb_default" \
  -F "tenant_id=tenant_default" \
  -F "permission=public"
```

每个 Qdrant payload 固定包含：

```text
text, kb_id, doc_id, chunk_id, file_name, file_type,
page, section, tenant_id, permission, created_at
```

查询时同时按 `kb_id`、`tenant_id`、`permission` 过滤。

### 文档切分策略

默认使用固定长度切分：

```dotenv
CHUNK_STRATEGY=fixed
CHUNK_SIZE=600
CHUNK_OVERLAP=100
```

若希望 Markdown 根据标题结构自适应切分，可改为：

```dotenv
CHUNK_STRATEGY=adaptive
MARKDOWN_HEADING_MAX_LEVEL=6
CHUNK_SIZE=1200
CHUNK_OVERLAP=100
```

`adaptive` 模式会先按 Markdown 的 `#` 到 `######` 标题分成章节，并把完整标题路径写入 Qdrant 的 `section` 字段，例如：

```text
员工制度 / 报销管理 / 审批流程
```

如果单个章节仍超过 `CHUNK_SIZE`，再在该章节内部按固定长度和重叠量二次切分。TXT 和 PDF 暂时继续使用固定长度切分。修改 `.env` 后需要重启 API 服务；已经入库的文档不会自动重新切分，需要重新上传或重建索引。

## 微信公众平台回调

回调地址：

```text
https://你的公网域名/wechat/callback
```

在微信公众平台填写与 `.env` 中 `WECHAT_TOKEN` 相同的 Token。微信首次配置会请求：

```text
GET /wechat/callback?signature=...&timestamp=...&nonce=...&echostr=...
```

服务按微信规则进行 SHA-1 校验，成功返回 `echostr`，失败返回 HTTP 403。

微信文本消息通过带有 `signature`、`timestamp`、`nonce` 查询参数的 `POST /wechat/callback` 进入系统，POST 同样会先校验签名。路由只负责 XML 解析、消息去重、构造 `ChatRequest`、调用 `handle_chat` 和构造 XML 回复；非文本消息回复“当前仅支持文本问题。”。首版去重缓存在单进程内存中，多实例部署应替换为 Redis。

## 有赞商品、订单与小程序卡片

该接入只开放只读查询：商品查询返回有赞商品小程序卡片；订单查询会在缺少手机号时追问，随后先通过手机号取得有赞客户 `yz_uid`，再查询该客户近期订单，并附“我的订单”小程序卡片。不包含退款、取消订单、改地址或其他写操作。

启用前需要在有赞云应用中取得只读能力和 `access_token`，并从有赞店铺后台复制真实的小程序参数：

```dotenv
YOUZAN_ENABLED=true
YOUZAN_ACCESS_TOKEN=replace_me
YOUZAN_KDT_ID=replace_me
YOUZAN_PRODUCT_PAGE_PATH_TEMPLATE=从商品推广入口复制并替换商品标识为{alias}
YOUZAN_MINI_PROGRAM_APP_ID=wx...
YOUZAN_MINI_PROGRAM_USER_NAME=gh_...@app
YOUZAN_MINI_PROGRAM_DISPLAY_NAME=萧岚苑
YOUZAN_MINI_PROGRAM_ICON_URL=https://稳定公网地址/icon.jpg
YOUZAN_ORDER_PAGE_PATH=从正式小程序“我的订单”卡片或后台复制
YOUZAN_ORDER_CARD_THUMB_URL=https://稳定公网地址/order.jpg
```

`YOUZAN_PRODUCT_PAGE_PATH_TEMPLATE` 支持 `{alias}`、`{item_id}`、`{kdt_id}`。不同有赞店铺类型的小程序路径可能不同，不要根据示例猜路径。Eyun 通过 `/sendApplets` 发送卡片；`icon` 和缩略图应使用稳定的公网 PNG/JPG。

当前使用配置中的 `YOUZAN_ACCESS_TOKEN`。正式启用前应在有赞控制台完成测试店铺授权，并由部署环境或有赞 Token 托管能力负责更新令牌。

### 有赞 AI Tool Call

MCP 地址为 `/mcp`，使用 `Authorization: Bearer <MCP_API_KEY>`。真实 AI/MCP 客户端可发现并调用以下只读工具：

- `youzan_search_products(keyword, limit)`：搜索在售商品。
- `youzan_get_product(item_id)`：读取商品详情。
- `youzan_list_inventory(limit)`：读取库存列表。
- `youzan_resolve_customer(customer_id, mobile, tenant_id, channel)`：用手机号验证并绑定客户身份。
- `youzan_search_customer_orders(customer_id, mobile, limit, tenant_id, channel)`：读取当前客户订单。
- `youzan_get_customer_order(customer_id, order_no, mobile, tenant_id, channel)`：校验订单归属后读取详情。

所有结果都包含 `read_only=true`、工具名和 `trace_id`。手机号在身份表中完整保存，用于后续真实查询；AI 返回值和 `youzan_tool_call_audits` 审计参数仅包含脱敏手机号。订单详情工具不会只凭订单号查询，必须先确认该订单属于当前已验证客户。有赞退款、取消、改价、改地址、发货和库存调整等写接口没有注册为 AI 工具。

### 生产数据目录

生产 Compose 将云服务器的 `/srv/intelligent-customer-service/data/` bind mount 到容器 `/app/data`，并把 Hugging Face 缓存单独挂载到 `/app/data/huggingface`。生产配置位于 `/etc/intelligent-customer-service/backend.env`；代码 checkout 不保存密钥、数据库、上传文件、缓存、备份或部署日志。完整运维说明见仓库根目录的 `docs/deployment.md`。

## 云服务配置

### Qdrant Cloud

设置真实 `QDRANT_URL` 和 `QDRANT_API_KEY`。collection 默认是 `knowledge_chunks`，服务首次写入时自动创建。`QDRANT_VECTOR_SIZE` 必须与 embedding 模型输出维度一致。

### LLM

支持 OpenAI-compatible 的 DeepSeek、OpenAI 和 DashScope：

```dotenv
LLM_PROVIDER=deepseek
LLM_MODEL=deepseek-chat
DEEPSEEK_API_KEY=...
```

也可把 `LLM_PROVIDER` 改为 `openai` 或 `dashscope`，并填写对应 Key 和模型名。

### Embedding

`EMBEDDING_PROVIDER=mock` 使用轻量确定性向量，便于 MVP 离线运行。

本地真实 BGE-M3：

```dotenv
EMBEDDING_PROVIDER=bge
EMBEDDING_MODEL=BAAI/bge-m3
QDRANT_VECTOR_SIZE=1024
```

`bge` provider 使用 `sentence-transformers` 加载 `BAAI/bge-m3`，输出 1024 维归一化向量。首次运行会从 Hugging Face 下载模型；下载完成后会复用本机缓存。

要调用 OpenAI-compatible embedding API：

```dotenv
EMBEDDING_PROVIDER=openai
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_API_KEY=...
EMBEDDING_BASE_URL=https://api.openai.com/v1
QDRANT_VECTOR_SIZE=1536
```

## 日志与质检接口

日志系统用于记录每一轮客服 AI 对话的完整处理链路，方便排查意图识别、路由、模板、RAG、最终回复、耗时和异常。日志查询接口都位于 `/api/v1/admin/...` 下，并复用 Bearer API Key 鉴权。

每条日志包含 `trace_id`、`channel`、`user_id`、`session_id`、`kb_id`、`tenant_id`、`permission`、`user_message`、`answer`、`route`、`reply_type`、`primary_intent`、`secondary_intents`、`sales_stage`、`confidence`、`template_id`、`template_score`、`next_action`、`sources`、`need_human`、`policy_reason`、`intent_reason`、`usage`、`latency_ms`、`stage_latencies`、`status`、`error_code`、`error_message` 和 `created_at`。`metadata` 会过滤 `token`、`password`、`api_key`、`secret`、`authorization` 等敏感字段。

### 查询日志列表

```bash
curl -H "Authorization: Bearer change_me" \
  "http://localhost:8000/api/v1/admin/chat-logs?page=1&page_size=50&route=template_reply"
```

响应示例：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "items": [
      {
        "trace_id": "request_xxx",
        "channel": "api",
        "user_id": "user_001",
        "session_id": "session_xxx",
        "user_message": "这个有点贵",
        "answer": "理解的，第一次入手确实会考虑价格。",
        "route": "template_reply",
        "reply_type": "template",
        "primary_intent": "price_objection",
        "secondary_intents": ["hesitation"],
        "sales_stage": "objection_handling",
        "confidence": 0.88,
        "template_id": "tpl_price_objection_001",
        "need_human": false,
        "sources": [],
        "usage": {},
        "latency_ms": 1230,
        "status": "success",
        "created_at": "2026-06-29T10:20:00+08:00"
      }
    ],
    "total": 1,
    "page": 1,
    "page_size": 50
  }
}
```

支持按 `user_id`、`session_id`、`route`、`primary_intent`、`template_id`、`status`、`need_human`、`keyword`、`start_time`、`end_time` 过滤。`keyword` 会同时匹配 `user_message` 和 `answer`。

### 查询日志详情

```bash
curl -H "Authorization: Bearer change_me" \
  "http://localhost:8000/api/v1/admin/chat-logs/request_xxx"
```

详情会额外返回 `template_score`、`policy_reason`、`intent_reason`、`stage_latencies` 和过滤后的 `metadata`。管理端 `metadata.decision` 保存精简的动作、原因和决策轨迹；业务事实、工具状态、完整 `ReplyPlan` 以及鉴权字段不会写入该轨迹，也不会通过聊天响应返回。如果 `trace_id` 不存在，响应：

```json
{
  "code": 40000,
  "message": "日志不存在",
  "data": null
}
```

### 查询日志统计

```bash
curl -H "Authorization: Bearer change_me" \
  "http://localhost:8000/api/v1/admin/chat-log-stats"
```

响应示例：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "total": 100,
    "success_count": 96,
    "failed_count": 4,
    "avg_latency_ms": 1180.5,
    "route_counts": {"template_reply": 60, "rag_answer": 20},
    "intent_counts": {"price_objection": 30, "ask_price": 20},
    "template_counts": {"tpl_price_objection_001": 18},
    "human_count": 5,
    "rag_count": 20,
    "template_count": 60
  }
}
```

排查建议：

- 意图识别错误：查看 `primary_intent`、`secondary_intents`、`confidence`、`intent_reason`。
- route 错误：查看 `route`、`policy_reason` 和 `stage_latencies.policy_ms`。
- 模板未命中：查看 `template_id` 是否为空，以及 `reply_type` 是否降级为 `unsupported` 或 `clarify`。
- RAG 无 sources：查看 `route` 是否为 `rag_answer` 或 `template_then_rag`，再检查 `sources` 和 Qdrant 配置。
- 延迟过高：先看 `latency_ms`，再根据 `stage_latencies` 定位到 intent、template、rag、reply_build 或 state_update 阶段。

配置项：

```dotenv
CHAT_LOG_ENABLED=true
CHAT_LOG_PROVIDER=sqlite
CHAT_LOG_DB_URL=sqlite:///./chat_logs.db
CHAT_LOG_RETENTION_DAYS=30
CHAT_LOG_MAX_MESSAGE_LENGTH=2000
CHAT_LOG_MAX_ANSWER_LENGTH=4000
```

当 `CHAT_LOG_ENABLED=false` 时，聊天主流程正常返回，日志不写入；admin 查询接口仍存在并返回空数据。

## 测试

```bash
python -m pytest -q
python -m compileall app
```

## 后续扩展

- 用 Redis 替换微信消息内存去重，并持久化会话历史。
- 为知识库、租户和权限增加关系数据库模型与管理 API。
- 增加 DOCX、OCR、表格和结构化章节解析。
- 接入 bge-reranker，支持更强的二阶段重排。
- 增加异步入库任务、状态查询、删除/重建索引和幂等键。
- 增加微信消息加解密、限流、审计、指标与分布式追踪。

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
            Policy Engine
                  ↓
 Template Engine / Knowledge RAG / Clarify / Human
                  ↓
            Reply Builder
                  ↓
             State Update
```

## 本地启动

要求 Python 3.11+。

```bash
cd wechat_rag_bot
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

其中意图识别、模板库、意图样本和用户状态都是 mock/内存实现。后续可把模板和意图样本接入 Qdrant，把状态接入 Redis/PostgreSQL，把 `INTENT_LLM_PROVIDER` 切到真实 LLM。

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
    "trace_id": "req_xxx"
  }
}
```

`answer`、`session_id`、`sources`、`usage` 是旧字段，保持兼容；后面的字段是新增路由与调试信息。

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

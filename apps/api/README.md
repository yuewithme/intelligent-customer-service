# 智能销售 API

这是萧岚苑智能销售系统的 FastAPI 后端，负责统一聊天入口、销售 Agent、客户状态与标签、知识检索、商品和订单查询，以及微信 / 易云 / 有赞等外部集成。

当前回复主链路由自主 Agent 负责：模型结合对话上下文和客户工作区，自行判断本轮商业目的、是否继续挖需、进入推品或调用工具。代码只保留结构契约、权限与安全边界、微信发送队列等必要硬约束。旧的 Intent Router、固定话术和影子回复链路不再参与生产决策。

完整设计以仓库级文档为准：

- [系统架构](../../docs/architecture.md)
- [Agent Harness 现役说明](../../docs/agent-harness-v2/README.md)
- [生产部署](../../docs/deployment.md)

## 本地启动

要求 Python 3.11+。

```powershell
cd apps/api
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
uvicorn app.main:app --reload
```

服务默认运行在 `http://127.0.0.1:8000`：

- OpenAPI：`/docs`
- 健康检查：`/health`
- 统一聊天接口：`POST /api/v1/chat`
- 微信回调：`/wechat/callback`
- 易云回调：`POST /eyun/callback`
- 有赞回调：`/youzan/callback`

除外部平台回调外，业务 API 默认需要 `Authorization: Bearer <API_KEY>`。仅限本地开发时可设置 `API_AUTH_ENABLED=false`。

## 配置原则

复制并按需填写 [`.env.example`](.env.example)。当前模型配置以 `LLM_PROVIDER` / `LLM_MODEL` 为通用默认值，并可按知识、客户画像、人格润色和审核等用途配置对应模型。

不要再新增或依赖 `INTENT_LLM_*`、`REPLY_SHADOW_*`、`TALK_SCRIPT_LLM_*`。这些键已退出应用 Settings 契约；旧部署环境即使暂时残留，也不会参与 Agent 运行。

本地离线测试可使用：

```dotenv
API_AUTH_ENABLED=false
QDRANT_URL=
EMBEDDING_PROVIDER=mock
LLM_PROVIDER=mock
```

运行数据、上传文件、数据库和缓存不属于源码。生产环境统一挂载到 `/srv/intelligent-customer-service/`，不得写入 Git checkout。

## 最小验证

```powershell
cd apps/api
python -m pytest tests/test_contracts.py tests/test_config_env.py tests/test_admin_web_deployment.py -q
```

Agent 或销售决策链路有改动时，应追加运行对应的 `test_agent_*`、`test_sales_*` 或相关领域测试。生产发布只允许通过仓库根目录的 `docker-compose.prod.yml` 和 `deploy/auto-deploy.sh` 完成。

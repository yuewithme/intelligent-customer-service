# AI Sales Agent

面向微信私域销售场景的 AI 销售 Agent，包含 FastAPI 销售编排后端和自主开发的 Vue 运营管理后台。

## Structure

- `wechat_rag_bot/`: FastAPI 后端服务，提供销售对话编排、微信回调、客户记忆、会话日志和受控人工接管 API。
- `admin-web/`: 自主开发的 Vue 3 + TypeScript 销售运营后台，包含销售工作台、活动管理和受控人工接管。
- `docs/`: 产品计划、开发计划和测试记录。

## Local Checks

Backend:

```powershell
cd wechat_rag_bot
python -m pytest tests/test_admin_conversations.py tests/test_wechat.py tests/test_chat_logs.py tests/test_handoff_fallbacks.py tests/test_chat_api.py -q
```

Frontend:

```powershell
cd admin-web
pnpm ts:check
pnpm build:local
```

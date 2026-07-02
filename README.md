# Intelligent Customer Service

智能客服产品仓库，包含 FastAPI 后端和 Vue 管理后台。

## Structure

- `wechat_rag_bot/`: FastAPI 后端服务，提供聊天、微信回调、会话日志和受控人工接管 API。
- `admin-web/`: Vue 3 + Element Plus 管理后台，包含受控人工接管工作台。
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

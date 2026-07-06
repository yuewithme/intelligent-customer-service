# Vercel + Render 部署说明

> 历史方案，当前已停用。现行生产部署请参考 `docs/docker-tunnel-deployment.md`，统一入口为 `http://124.160.45.66:21873`。

这个方案不需要自建云服务器：

- Render 跑 FastAPI 后端，负责 webhook、AI 回复、会话和管理 API。
- Vercel 跑管理后台前端，并用 Serverless Function 代理后台 API。
- 后台不做完整登录系统，只做一个简单访问门禁。

## 1. Render 后端

仓库根目录已经提供 `render.yaml`。在 Render 里选择 Blueprint 部署这个仓库即可。

后端服务名：

```text
intelligent-customer-service-api
```

必须配置的 Render 环境变量：

```text
API_KEY=一串长随机密钥
WECHAT_TOKEN=你的 webhook token
WECHAT_APP_ID=你的微信/渠道 app id
WECHAT_APP_SECRET=你的微信/渠道 secret
EYUN_BASE_URL=http://www.eyunz.com/wx-api
EYUN_AUTHORIZATION=控制台里的 Eyun Authorization
EYUN_WID=当前登录微信实例 wId
DEEPSEEK_API_KEY=你的模型 key
```

如果启用知识库检索，再配置：

```text
QDRANT_URL=你的 Qdrant 地址
QDRANT_API_KEY=你的 Qdrant key
```

Render 会挂载持久磁盘到：

```text
/var/data
```

SQLite 和上传文件会保存到这里：

```text
DATABASE_URL=sqlite:////var/data/rag.db
CHAT_LOG_DB_URL=sqlite:////var/data/chat_logs.db
UPLOAD_DIR=/var/data/uploads
```

webhook 回调地址填 Render 后端地址：

```text
https://你的-render-service.onrender.com/wechat/callback
```

## 2. Vercel 前端

在 Vercel 导入同一个 GitHub 仓库，Root Directory 选择：

```text
admin-web
```

Vercel 会读取 `admin-web/vercel.json`：

```text
Build Command: pnpm build:prod
Output Directory: dist-prod
Install Command: pnpm install --frozen-lockfile
```

必须配置的 Vercel 环境变量：

```text
BACKEND_BASE_URL=https://你的-render-service.onrender.com
BACKEND_API_KEY=和 Render 后端 API_KEY 相同
ADMIN_GATE_PASSWORD=你打开后台时输入的门禁密码
ADMIN_GATE_SECRET=另一串长随机密钥
ADMIN_GATE_ENABLED=true
```

浏览器不会直接拿到 `BACKEND_API_KEY`。前端请求 `/api/...` 时，由 Vercel Function 转发到 Render，并在服务端注入后端 API Key。

## 3. CLI

本机已安装：

```powershell
D:\Apps\npm-global\vercel.cmd --version
D:\Apps\render-cli\render.cmd --version
```

登录 Vercel：

```powershell
D:\Apps\npm-global\vercel.cmd login
```

部署前端：

```powershell
cd D:\Codex产品开发\智能客服\admin-web
D:\Apps\npm-global\vercel.cmd --prod
```

登录 Render：

```powershell
D:\Apps\render-cli\render.cmd login
```

Render CLI 需要登录并选择 workspace 后，才能校验 Blueprint：

```powershell
cd D:\Codex产品开发\智能客服
D:\Apps\render-cli\render.cmd blueprints validate render.yaml
```

## 4. 访问路径

后台：

```text
https://你的-vercel-app.vercel.app
```

后台 API 代理：

```text
https://你的-vercel-app.vercel.app/api/v1/admin/conversations
```

webhook：

```text
https://你的-render-service.onrender.com/wechat/callback
```

## 5. 注意

- 不要把 `API_KEY` 写进前端 `VITE_*` 环境变量。
- 后台门禁只是轻量门禁，不替代完整用户系统。
- Render 持久磁盘需要付费 Web Service；免费服务文件系统是临时的，不适合长期保存 SQLite 会话数据。

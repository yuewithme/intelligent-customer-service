# 本机 Docker + 内网穿透生产部署

当前生产环境统一使用本机 Docker，不再使用 Render 或 Vercel 承载业务流量。

## 生产地址

| 用途 | 地址 |
| --- | --- |
| 管理后台 | `http://124.160.45.66:21873/gate?redirect=/workbench` |
| API 基地址 | `http://124.160.45.66:21873` |
| 微信回调 | `http://124.160.45.66:21873/wechat/callback` |
| 健康检查 | `http://124.160.45.66:21873/health` |
| 本机运维 | `http://localhost:21873` |

所有前端、API 和微信回调均通过同一个公网地址进入 Nginx，再由 Nginx 将 `/api/`、`/wechat/` 和 `/health` 转发给 Docker 内部的 FastAPI `8000` 端口。

工作台通过 `/api/v1/admin/conversations/events` 建立 SSE 长连接。Nginx 必须保持该路径的 `proxy_buffering off` 和较长的 `proxy_read_timeout`，否则实时事件可能被缓存或提前断开。

## Docker 服务

```text
公网 124.160.45.66:21873
  -> 内网穿透
  -> 本机 21873
  -> admin-web / Nginx :80
  -> api / FastAPI :8000
  -> backend-data /app/data
```

`backend-data` 保存 `rag.db`、`chat_logs.db` 和上传文件。重建容器不会删除数据卷。

`HF_HOME=/app/data/huggingface` 将本地 Embedding 模型缓存放入同一个持久卷，避免每次重建容器后重新下载 BGE 模型。BGE 的加载和编码在工作线程运行，不得阻塞 FastAPI 事件循环，否则 Webhook 会因等待超时出现 Nginx `499`。

## 运维命令

```powershell
docker compose -p intelligent-customer-service -f docker-compose.prod.yml up -d --build
docker compose -p intelligent-customer-service -f docker-compose.prod.yml ps
docker compose -p intelligent-customer-service -f docker-compose.prod.yml logs --tail 100
docker compose -p intelligent-customer-service -f docker-compose.prod.yml down
```

不要执行 `docker compose down -v`，否则持久化数据卷会被删除。

## 切换要求

- 易云或微信平台的回调地址必须设置为 `http://124.160.45.66:21873/wechat/callback`。
- `/wechat/callback` 必须快速返回 `{"code":"1000","message":"success"}`；Nginx 对该路径关闭响应缓冲。
- 防火墙和内网穿透规则需保持 TCP `21873` 可访问。
- Docker Desktop 和两个业务容器必须持续运行。
- Render/Vercel 属于历史部署，不再作为生产入口。

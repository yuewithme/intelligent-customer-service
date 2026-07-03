# 线上部署说明

当前里程碑建议先用单机 Docker Compose 部署：

- `admin-web`: Nginx 托管 Vue 静态资源，并反代 `/api`、`/wechat`、`/health` 到后端。
- `api`: FastAPI 后端。
- `backend-data`: Docker volume，保存 SQLite 数据库和上传文件。

## 服务器准备

安装 Docker 和 Docker Compose 插件后，拉取仓库：

```bash
git clone https://github.com/yuewithme/intelligent-customer-service.git
cd intelligent-customer-service
```

## 配置环境变量

```bash
mkdir -p deploy/env
cp deploy/env/backend.prod.env.example deploy/env/backend.prod.env
```

编辑 `deploy/env/backend.prod.env`，至少修改：

- `API_KEY`
- `WECHAT_TOKEN`
- `WECHAT_APP_ID`
- `WECHAT_APP_SECRET`
- `DEEPSEEK_API_KEY` 或实际使用的模型供应商 key
- `QDRANT_URL` / `QDRANT_API_KEY`，如果启用知识库检索

## 启动

```bash
docker compose -p smart-customer-service -f docker-compose.prod.yml up -d --build
```

检查：

```bash
docker compose -p smart-customer-service -f docker-compose.prod.yml ps
curl http://127.0.0.1/health
```

访问后台：

```text
http://服务器IP/
```

登录时输入 `backend.prod.env` 中的 `API_KEY`。

## 反向代理与域名

如果服务器上已有 Nginx，可以让外层 Nginx 反代到本 Compose 暴露的 `80` 端口，或把 `docker-compose.prod.yml` 的端口改成 `127.0.0.1:8080:80`。

微信回调地址示例：

```text
https://你的域名/wechat/callback
```

## 数据持久化

当前 SQLite 和上传文件保存在 Docker volume `backend-data`：

```bash
docker volume ls
docker volume inspect intelligent-customer-service_backend-data
```

正式生产建议后续升级：

- SQLite -> PostgreSQL
- 本地上传目录 -> 对象存储
- 单机 Docker Compose -> 云厂商容器服务或 Kubernetes

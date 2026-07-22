# 云服务器生产部署

生产环境只使用腾讯云服务器上的 Docker Compose，不再维护 Render、Vercel 或内网穿透部署。

## 目录边界

```text
/home/ubuntu/intelligent-customer-service/  # Git 代码，只允许 fast-forward 更新
/etc/intelligent-customer-service/backend.env
/srv/intelligent-customer-service/data/    # SQLite、上传文件、运行时媒体
/srv/intelligent-customer-service/cache/huggingface/
/srv/intelligent-customer-service/backups/
/srv/intelligent-customer-service/logs/
```

生产密钥、数据库、上传文件、缓存、备份和日志都不得放入 Git checkout。

## 服务入口

| 用途 | 地址 |
| --- | --- |
| 管理后台 | `http://150.158.52.233/gate?redirect=/workbench` |
| API 基地址 | `http://150.158.52.233` |
| 微信回调 | `http://150.158.52.233/wechat/callback` |
| 健康检查 | `http://150.158.52.233/health` |

`admin-web` 容器中的 Nginx 承载 Vue 静态资源，并把 `/api/`、`/wechat/`、`/youzan/`、`/static/` 和 `/health` 转发给 FastAPI。

## 首次准备

```bash
sudo install -d -m 755 /etc/intelligent-customer-service
sudo install -d -o ubuntu -g ubuntu \
  /srv/intelligent-customer-service/data \
  /srv/intelligent-customer-service/cache/huggingface \
  /srv/intelligent-customer-service/backups \
  /srv/intelligent-customer-service/logs
sudo install -m 600 deploy/env/backend.prod.env.example \
  /etc/intelligent-customer-service/backend.env
sudo chown root:root /etc/intelligent-customer-service/backend.env
```

编辑 `backend.env`，至少配置 API、MCP、微信、易云和实际使用的模型供应商密钥。生产配置文件只能由 root 读取，不进入 Git。

## 手动部署

```bash
cd /home/ubuntu/intelligent-customer-service
bash deploy/auto-deploy.sh --force
```

部署脚本只接受 `origin/main` 的 fast-forward 更新，会验证配置和数据目录、构建两个容器并等待 `/health` 成功。

## 自动部署

服务器的 `ubuntu` 用户使用以下 cron，每五分钟检查一次：

```cron
*/5 * * * * bash /home/ubuntu/intelligent-customer-service/deploy/auto-deploy.sh >/dev/null 2>&1
```

部署日志写入 `/srv/intelligent-customer-service/logs/auto-deploy.log`。

## 数据备份

- `data/` 是必须备份的生产数据。
- `cache/huggingface/` 可重新生成，不进入普通业务备份。
- SQLite 备份应使用 SQLite backup API 或在暂停 API 写入后复制，不得直接复制正在写入的数据库文件。
- 禁止执行 `docker compose down -v`，也禁止在未验证备份的情况下删除 `/srv/intelligent-customer-service/data`。

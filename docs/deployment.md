# 云服务器生产部署

生产环境使用腾讯云服务器上的 Docker Compose。GitLab `main` 是唯一代码来源，GitLab CI 是正式发布入口；服务器保留的定时轮询只作为补偿机制。

## 现役拓扑

| 角色 | 地址或名称 | 边界 |
| --- | --- | --- |
| GitLab 仓库 | `wohu-apps/intelligent-customer-service` | `main` 为生产分支 |
| GitLab Runner | `hz-build-01`，VPC `10.200.5.13` | 执行流水线，不承载业务 |
| 受限 SSH 跳板 | `150.158.52.233` | 只允许 Runner 转发到生产机 `22` 端口，不部署代码和服务 |
| 生产机 | `43.143.83.26`，VPC `10.200.5.17`，`VM-5-17-ubuntu` | 拉取 GitLab 代码并运行 Docker Compose |
| 正式入口 | `https://sales-agent.hzwohu.com` | 对外提供管理端与 API |

发布路径固定为：

```text
push main -> GitLab Runner -> 150 受限跳板 -> 43.143.83.26 生产机 -> 线上验收
```

150 只解决 Runner 到生产机的网络路由问题，不是生产服务器。不要在 150 上拉取、构建或运行本项目。

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
| 登录后进入工作台 | `https://sales-agent.hzwohu.com/gate?redirect=/workbench` |
| 工作台 | `https://sales-agent.hzwohu.com/workbench` |
| API 基地址 | `https://sales-agent.hzwohu.com` |
| 微信回调 | `https://sales-agent.hzwohu.com/wechat/callback` |
| 健康检查 | `https://sales-agent.hzwohu.com/health` |

`admin-web` 容器中的 Nginx 承载 Vue 静态资源，并把 `/api/`、`/wechat/`、`/youzan/`、`/static/` 和 `/health` 转发给 FastAPI。

## 首次准备

```bash
sudo install -d -o root -g docker -m 750 /etc/intelligent-customer-service
sudo install -d -o ubuntu -g ubuntu \
  /srv/intelligent-customer-service/data \
  /srv/intelligent-customer-service/cache/huggingface \
  /srv/intelligent-customer-service/backups \
  /srv/intelligent-customer-service/logs
sudo install -o root -g docker -m 640 deploy/env/backend.prod.env.example \
  /etc/intelligent-customer-service/backend.env
```

编辑 `backend.env`，至少配置 API、MCP、微信、易云和实际使用的模型供应商密钥。文件不进入 Git，内容也不得出现在流水线日志中。

## GitLab 自动发布

`.gitlab-ci.yml` 的三个阶段缺一不可：

1. `preflight:production` 通过腾讯云元数据核对公网 IP，检查生产目录、GitLab remote、目标 SHA、受控环境文件、数据目录和 Docker Compose。
2. `deploy:production` 使用固定的 SSH 主机密钥，经 150 受限跳板进入生产机，执行 `deploy/auto-deploy.sh --force`。
3. `verify:production` 核对生产 `HEAD` 与 Pipeline SHA 完全一致，并验证公开 `/health` 和当前前端资源。

部署任务由 `resource_group: sales-agent-production` 串行化。生产脚本还使用文件锁：CI 的 `--force` 最多等待 20 分钟，等待超时即失败；无参数的服务器定时轮询遇到锁会立即退出，避免两个构建互相覆盖。

Runner 和生产机之间采用最小权限 SSH：跳板机上的 Runner 公钥只允许转发到 `43.143.83.26:22`，生产机只接受来自跳板公网地址的该公钥。不得关闭 `StrictHostKeyChecking`、复制私钥到仓库，或为了 CI 向公网放开生产机 SSH。

## 服务器补偿轮询与手动部署

生产机当前启用 `ics-auto-deploy.timer`，每 2 分钟触发一次无参数部署脚本。它只在 `origin/main` 有新提交且部署锁空闲时工作；GitLab CI 仍是正式发布和验收记录的来源。

需要人工重跑时，在确认目标确实是 `VM-5-17-ubuntu` 后执行：

```bash
cd /home/ubuntu/intelligent-customer-service
bash deploy/auto-deploy.sh --force
```

部署脚本只接受 `origin/main` 的 fast-forward 更新，会验证配置和数据目录、构建容器并等待本机 `/health` 成功。生产 Compose 固定使用传统 Docker 构建器，规避该服务器上 BuildKit 导出阶段无进展的问题；Debian 软件包使用腾讯云镜像，避免 `ffmpeg` 等依赖从默认源下载过慢。日志写入 `/srv/intelligent-customer-service/logs/auto-deploy.log`。

## 排障经验

- 先确认机器身份：参考服务器、Runner、跳板和生产机是四个角色；只有元数据公网 IP 为 `43.143.83.26` 的机器可以部署。
- SSH 在认证前超时通常是路由或安全组问题，不是密钥问题；Runner 无法直连生产 VPC 时使用现有受限跳板。
- `Host key verification failed` 表示信任链未固定，应核验并固定 ED25519 主机密钥，不能绕过校验。
- 部署任务退出 0 不等于上线成功。必须用独立验证阶段比对生产 SHA、公开健康检查和前端资源，否则锁冲突可能造成“假成功”。
- 遇到长构建先分层定位：下载无进展检查软件源，镜像导出无磁盘活动检查 BuildKit，不要把所有慢构建都归因于网络。
- Compose 重建切换期间可能出现短暂 `502`；健康检查恢复只能证明最终可用，不代表零停机。对无中断要求提高时，应另行设计滚动或蓝绿发布。

生产机的 `ics-auto-deploy.service` 描述和环境文件覆盖仍保留旧 Gitea 时代文字，属于已知配置漂移；当前 GitLab CI 显式验证 `/etc/intelligent-customer-service/backend.env`。调整该 systemd 单元前应单独评审，不能在文档收口时顺手修改生产状态。

## 数据备份

- `data/` 是必须备份的生产数据。
- `cache/huggingface/` 可重新生成，不进入普通业务备份。
- SQLite 备份应使用 SQLite backup API 或在暂停 API 写入后复制，不得直接复制正在写入的数据库文件。
- 禁止执行 `docker compose down -v`，也禁止在未验证备份的情况下删除 `/srv/intelligent-customer-service/data`。

# 项目架构与目录契约

本文档是仓库结构、模块归属、配置位置和运行产物位置的唯一入口。新增功能应先确定
归属，再创建文件；不要在仓库根目录或 `app/` 下新增平铺的业务模块。

## 1. 系统边界

```text
用户 / 微信 / 易云 / 有赞
          │
          ▼
apps/admin (Nginx + Vue) ─────► apps/api (FastAPI)
                                      │
                    ┌─────────────────┼─────────────────┐
                    ▼                 ▼                 ▼
                 领域模块          外部集成          基础设施
              app/domains       app/integrations   app/infrastructure
                    │                 │                 │
                    └─────────────────┴─────────────────┘
                                      │
                               /srv/.../data（生产）
                               var/runtime（本地）
```

公网只进入 `apps/admin` 的 Nginx。Nginx 提供前端静态文件并代理 API；FastAPI 不再
重复托管前端构建产物。

## 2. 顶层目录

| 目录 | 唯一职责 | 是否进入 Git |
| --- | --- | --- |
| `apps/api` | 后端应用、后端测试和应用专用评测执行器 | 是，排除本地环境与数据 |
| `apps/admin` | 前端源码、构建配置和生产 Nginx | 是，排除依赖与构建结果 |
| `datasets` | 人工维护、可复现的评测集、测试样本和确认基线 | 是 |
| `deploy` | 生产部署脚本和脱敏配置模板 | 是 |
| `docs` | 当前文档、参考资料和历史归档 | 是 |
| `var` | 数据库、缓存、评测输出、导入文件、构建产物、临时文件 | 否 |

历史方案只放在 `docs/archive/`，不得继续作为当前实现依据。评测执行结果写入
`var/evaluation/results/`；只有经过确认且用于回归比较的基线才能进入
`datasets/evaluation/baselines/`。

## 3. 后端内部结构

```text
apps/api/app/
  bootstrap/            应用装配：路由、生命周期、异常和静态资源
  core/                 配置、鉴权、日志、ID、时间等横切能力
  domains/
    access/             后台访问门禁
    catalog/            商品、兰花资料与商品知识
    conversations/      会话、消息、日志、状态与聊天入口
    customers/          客户画像、等级和长期记忆
    decisioning/        意图、策略、Persona、模板与回复规划
    handoff/            人工接管与通知
    knowledge/          文档知识、向量检索、RAG 与重排
    sales/              销售阶段、标签、活动、SOP 与养护手册
  infrastructure/
    database/           SQLAlchemy 会话和持久化模型
  integrations/
    ai/ eyun/ mcp/ web/ wechat/ youzan/
  shared/               少量真正跨领域的稳定契约
  main.py               只保留 ASGI 入口
```

每个业务领域内部按需使用：

- `api/`：HTTP 参数接收、鉴权依赖和响应转换，不写业务规则。
- `schemas/`：该领域拥有的输入、输出和内部契约。
- `services/`：业务用例、规则和编排。
- `workers/`：仅在确有后台循环任务时使用。

依赖规则：

1. `bootstrap` 可以装配所有模块，但业务模块不得反向依赖 `bootstrap` 或 `main`。
2. API 层调用 service；service 不得导入 API/router。
3. 外部供应商协议只放在 `integrations`，领域模块不猜测供应商响应结构。
4. 跨领域调用必须指向明确的领域 service/schema，不再通过全局平铺目录新增模块。
5. `app/services` 与 `app/routers` 仅是旧测试和脚本的惰性兼容入口，禁止新增实现文件。
6. 数据库模型暂时集中在 `infrastructure/database/models.py`。在引入正式迁移工具前，
   不按领域拆表模型，避免 SQLite 生产结构出现不可控漂移。

## 4. 配置契约

配置契约的意思是：每一种配置只有一个权威定义、一个明确的使用环境和一个可检查的
示例，不允许靠“某个目录里可能还有一个 `.env`”来运行。

| 文件/位置 | 用途 | 内容限制 |
| --- | --- | --- |
| `apps/api/.env.example` | 后端完整配置字段清单 | 可提交，只能是示例值 |
| `apps/api/.env` | 本地后端真实配置 | 不提交 |
| `apps/admin/.env.development` | 前端开发构建配置 | 只允许 `VITE_*` 公共值 |
| `apps/admin/.env.production` | 前端生产构建配置 | 只允许 `VITE_*` 公共值 |
| `deploy/env/backend.prod.env.example` | 生产必填项模板 | 可提交，只能是示例值 |
| `/etc/intelligent-customer-service/backend.env` | 云服务器真实生产配置 | 不在 Git，权限 `600` |

后端测试会校验 `Settings`、完整示例和生产模板是否一致。新增配置必须同时修改
`Settings` 和对应示例；密钥不得写入前端环境变量。

## 5. 运行产物隔离

运行产物隔离的意思是：源码目录只保存“可以评审和复现的输入”，进程产生的可变内容
全部进入专用数据区。

- 本地统一进入 `var/`，例如 `var/runtime/`、`var/evaluation/results/`、
  `var/imports/`、`var/artifacts/` 和 `var/tmp/`。
- 生产业务数据进入 `/srv/intelligent-customer-service/data/`。
- 生产模型缓存进入 `/srv/intelligent-customer-service/cache/huggingface/`。
- 生产密钥进入 `/etc/intelligent-customer-service/backend.env`。
- Docker 镜像只包含源码和必要静态种子，不包含数据库、上传、缓存或本地 `.env`。

## 6. 部署统一

部署统一的意思是：开发、评审和生产都围绕同一份 Git `main` 和同一份
`docker-compose.prod.yml`，不再维护 Render、Vercel 或其他演示平台的旁路配置。

发布流程固定为：本地开发与测试 → 提交并推送 `main` → 云服务器 fast-forward 拉取 →
Compose 构建 `apps/api` 与 `apps/admin` → `/health` 验证。服务器只部署提交后的代码，
不直接修改源码。

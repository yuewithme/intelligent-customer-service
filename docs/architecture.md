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
    decisioning/        自主 Agent 决策、工具、结构化回复与兼容评测资产
    handoff/            人工接管与通知
    knowledge/          文档知识、向量检索、RAG 与重排
    orchestration/      能力目录、经验包契约和能力工作台
    sales/              服务中素材触达、客户标签、活动与养护手册
  infrastructure/
    database/           SQLAlchemy 会话、商品存储初始化和持久化模型
  integrations/
    ai/ eyun/ feishu/ mcp/ web/ wechat/ youzan/
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
5. 旧的 `app/services`、`app/routers` 与 `app/schemas` 平铺入口已移除；应用与测试都必须从现役领域或集成路径导入。
6. 数据库模型暂时集中在 `infrastructure/database/models.py`。在引入正式迁移工具前，
   不按领域拆表模型，避免 SQLite 生产结构出现不可控漂移。
7. schema 和 infrastructure 不反向导入业务 service、API 或 bootstrap。
   商品知识与有赞同步共用 `infrastructure/database/product_store.py` 的
   `get_product_session()`，不得通过同步服务的私有函数获取数据库会话。
   SOP 节点定义由 `handoff/schemas/handoff_notification.py` 提供，供请求校验、
   通知服务和能力工作台共享，校验过程不再反向加载通知服务。

### 核心调用链与职责

| 链路 | 主要模块与边界 |
| --- | --- |
| 应用装配 | `bootstrap/application.py` 创建 FastAPI；`routes.py` 注册业务路由；`lifecycle.py` 管理发送、媒体、同步和记忆等后台任务。 |
| 统一聊天 | 渠道入口 → `channel_service` 归一化 → `chat_orchestrator` 加载会话状态、画像、记忆并组装客户工作区 → `agent_runtime` 运行 Agent → 更新状态、会话与日志。 |
| Agent 执行 | `schemas/execution.py` 定义共享执行上下文；`agent_runtime` 负责模型轮次、工具预算、重写和最终回复；`agent_reply_policy` 负责事实、权限、开场、质量与对话推进检查；`agent_tools` 负责工具执行。回复策略模块只依赖 schema，不调用模型、数据库或外部服务。 |
| 商品与资料 | catalog 维护商品知识与导入；有赞同步负责供应商数据映射和同步；两者通过商品存储入口访问现有表。两套兰花导入器共用 `orchid_products/import_support.py` 的数据结构、表头读取、单元格规范化与知识片段构造。 |
| 微信收发 | 易云回调处理渠道事件；媒体识别、消息聚合、发送排队和回执确认由 eyun 集成承担；会话服务维护工作台可见消息与人工接管状态。供应商协议与重试语义不由 Agent 校验模块处理。 |
| 客户记忆 | customers 维护画像、事件、身份、记忆提取与检索；原始会话事件经双写进入持久化任务，读取仍受现有 rollout 配置控制。 |
| 销售与接管 | sales 管理标签、活动、手册和定时素材触达；handoff 维护接管配置与通知；orchestration 将能力和经验包展示到工作台，并提供现有 SOP 节点接管设置入口。 |
| 运营前端 | 路由与门禁 → SalesLayout → 业务页面 → `src/api` → Axios。工作台通过会话事件和兜底轮询同步；跨页面时间格式化放在 `src/utils/time.ts`，保留各页面原有的上海时区或浏览器本地时区显示方式。 |

商品存储提取保留原有表初始化、历史别名补齐和按 `database_url` 缓存会话工厂的行为。
会话及消息发送控制使用的 `chat_log_db_url` 与其他领域存储仍按各自配置访问，不能只因
都使用 SQLite 就合并。运维导入、数据回填和评测脚本也是有效入口，删除模块时必须一并
核对 `apps/api/scripts`、`apps/api/app/cli` 与 `apps/api/evaluation` 的引用。

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

<div align="center">

# 🌿 智能客服 · AI Sales Agent

**面向微信私域的智能销售与客户服务工作台**

让客户咨询、商品知识、销售跟进与人工服务在同一个工作流中协同。

**微信接待 · AI Agent · 知识检索 · 客户画像 · 人工接管**

[功能亮点](#-功能亮点) · [本地开发](#-本地开发) · [文档导航](#-文档导航)

</div>

---

## ✨ 项目介绍

智能客服是一套面向**微信私域销售与售后服务**的业务系统，由 FastAPI 后端和 Vue 运营后台组成。系统结合客户会话、商品资料和知识库，支持 AI 辅助接待、客户信息管理、商品与订单查询，以及人工接管。

当前业务围绕**兰花商品咨询、养护知识与客户服务**展开：运营人员可以在工作台查看消息、维护客户标签、查阅订单和养护资料，并按需要接手对话。商品、知识、活动和接管设置由对应的管理页面维护。

> **从一次咨询到持续服务：** 接收微信消息 → 理解客户上下文 → 检索资料与调用业务工具 → 生成回复 → 持续跟进或交由人工处理。

## 💡 功能亮点

| 能力 | 业务用途 |
| --- | --- |
| 💬 **会话工作台** | 集中查看会话与消息，在同一页面处理回复、客户信息和服务上下文。 |
| 🧠 **AI 接待与决策** | 结合会话状态、客户画像和工具生成回复，并通过回复策略检查事实、权限与对话质量。 |
| 📚 **知识库与商品资料** | 管理文档、商品和兰花知识，支持向量检索、RAG 与重排。 |
| 👤 **客户画像与记忆** | 维护客户标签、画像和长期记忆；新版记忆能力按配置逐步启用。 |
| 🛍️ **有赞业务集成** | 同步和查询商品与订单；物流等能力需要对应授权与配置。 |
| 🤝 **人工接管与通知** | 配置接管与通知，让运营人员介入需要人工处理的会话。 |
| 🌱 **销售服务运营** | 维护活动、养护手册和素材，支持服务过程中的定时素材触达。 |
| 🔐 **账号与权限** | 通过后台账号登录，按页面和员工微信范围分配访问权限。 |

> 外部渠道、模型和业务集成需要有效凭据。代码中提供的能力不代表每个环境都已启用。

## 🧩 技术架构

| 层次 | 技术与职责 |
| --- | --- |
| **运营前端** | Vue 3、TypeScript、Vite、Element Plus、Pinia、UnoCSS |
| **业务后端** | Python、FastAPI、SQLAlchemy，按业务领域组织路由与服务 |
| **知识与模型** | Qdrant 向量检索、RAG、模型供应商集成与 Agent 工具调用 |
| **外部渠道** | 微信 / 易云、有赞、飞书通知、MCP |
| **部署交付** | GitLab CI、Docker Compose、Nginx |

```text
微信客户 / 运营人员
        │
        ▼
   Nginx 统一入口
        ├── Vue 运营后台
        └── FastAPI 业务接口
                 ├── 会话 · 客户 · 商品 · 销售 · 人工接管
                 ├── Agent 决策 · 知识检索 · 业务工具
                 └── 数据存储 · 模型服务 · 外部渠道
```

生产环境由 Nginx 提供前端静态资源并代理 API。详细模块边界见 [架构与目录契约](docs/architecture.md)。

## 🗂️ 仓库结构

```text
apps/
  api/                 FastAPI、领域逻辑、外部渠道集成
  admin/               Vue 3 运营后台与生产 Nginx
datasets/
  evaluation/          可复现的评测集与已确认基线
  test-samples/        人工维护的测试样本
deploy/                生产环境变量模板与自动部署脚本
docs/                  架构、部署、参考资料与历史归档
var/                   本地数据库、缓存、评测结果、导入和临时产物（不进 Git）
docker-compose.prod.yml
```

完整边界与模块依赖规则见 [docs/architecture.md](docs/architecture.md)，生产部署见
[docs/deployment.md](docs/deployment.md)。

## 🚀 本地开发

### 1. 准备环境

- **Python 3.11**：与后端生产镜像保持一致。
- **Node.js 20.19+、pnpm 8.6+**：前端项目声明的最低版本要求。
- 按使用范围准备模型服务、Qdrant 和外部渠道配置。

以下命令使用 **PowerShell**，从仓库根目录开始执行。

### 2. 启动后端

```powershell
cd apps/api
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
Copy-Item .env.example .env
```

编辑 `apps/api/.env`，将占位值替换为实际配置：

| 配置 | 说明 |
| --- | --- |
| `API_KEY`、`MCP_API_KEY` | 设置自己的访问密钥。 |
| `ADMIN_GATE_PASSWORD`、`ADMIN_GATE_SECRET` | 配置后台初始管理员密码与门禁签名。 |
| `LLM_PROVIDER`、`LLM_MODEL` 及供应商凭据 | 设置实际使用的模型与访问密钥。 |
| `QDRANT_URL`、`QDRANT_API_KEY` | 配置知识检索使用的向量数据库。 |
| 微信、易云、有赞、飞书相关配置 | 根据接入范围填写凭据，并检查功能开关。 |

> **账号初始化：** 默认管理员用户名为 `wohukeji`，可通过 `ADMIN_GATE_USERNAME` 指定。请显式设置 `ADMIN_GATE_PASSWORD`；账号创建后，修改环境变量不会覆盖已有密码。详见 [账号初始化与权限](docs/deployment.md#后台账号初始化与权限)。

完成配置后启动：

```powershell
uvicorn app.main:app --reload
```

### 3. 启动前端

另开一个终端，从仓库根目录执行：

```powershell
cd apps/admin
pnpm install --frozen-lockfile
pnpm dev
```

| 本地入口 | 地址 |
| --- | --- |
| **运营后台** | [localhost:5173](http://localhost:5173) |
| **后端健康检查** | [127.0.0.1:8000/health](http://127.0.0.1:8000/health) |

开发服务器将 `/api` 和 `/health` 请求代理到本地 `8000` 端口。微信回调等外部访问需要另外配置可达地址。

<details>
<summary><strong>🛠️ 按改动范围选择开发检查命令</strong></summary>

从仓库根目录执行，按实际改动选择后端检查或前端构建：

```powershell
cd apps/api
python -m pytest tests/test_contracts.py tests/test_config_env.py tests/test_admin_web_deployment.py -q

cd ../admin
pnpm ts:check
pnpm build:prod
```

仅修改文档时，无需运行应用测试套件。

</details>

## 📖 文档导航

| 文档 | 适合什么时候阅读 |
| --- | --- |
| [架构与目录契约](docs/architecture.md) | 理解模块边界、调用链、配置归属和数据目录。 |
| [生产部署指南](docs/deployment.md) | 配置生产环境、了解发布流程、初始化账号和排查部署问题。 |
| [开发记录](docs/development/) | 查阅各次任务的目标、实现结果与验证记录。 |
| [后端配置示例](apps/api/.env.example) | 查找应用配置字段及示例值。 |
| [生产配置模板](deploy/env/backend.prod.env.example) | 准备生产环境所需配置。 |

## 🔄 仓库同步与发布

- **GitLab 主仓库：** [wohu-apps / intelligent-customer-service](https://git.hzwohu.com/wohu-apps/intelligent-customer-service)，`origin/main` 是生产代码来源。
- **GitHub 同步仓库：** [yuewithme / intelligent-customer-service](https://github.com/yuewithme/intelligent-customer-service)，用于同步项目代码与 README。
- **发布流程：** 本地开发与验证 → 推送 GitLab `main` → GitLab CI → 生产机快进拉取 → Docker Compose 构建 → 健康检查与版本验收。

**配置与数据分离：** 真实 `.env`、密钥、数据库、上传文件和运行缓存保留在本地或专用运行目录，不进入版本库。服务器用于部署和运行，源码修改在本地完成。

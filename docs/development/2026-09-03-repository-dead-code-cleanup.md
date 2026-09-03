# 全仓库废弃代码清理

## 任务目标

以当前生产 FastAPI 入口、Vue 路由和 Docker Compose 部署链路为准，删除重构后遗留且已确认无调用方的代码、脚本、配置、资源和生产依赖；同时修复审计中发现的环境变量模板漂移与 Eyun 回调代理缺失。

## 实施计划

1. 清理旧 Intent Router/taxonomy 残留、孤立页面、孤立服务、无引用静态资源和未使用依赖。
2. 删除现役文件内经全仓库引用检查确认无调用的函数、类、前端 API 包装和类型。
3. 将测试从 `app.services` / `app.routers` 兼容入口迁移到现役领域路径，然后删除兼容层。
4. 清理已无运行时读取的 Settings 字段及环境模板键，补齐现役语音、视频、购买标签和有赞加密配置。
5. 在 Nginx 增加 `/eyun/` 反向代理，保证已注册回调能通过现役公网入口到达 API。
6. 运行静态引用复查、前端类型检查与构建、后端最小基线及直接受影响测试，审查最终差异后提交并推送。

## 明确保留边界

- Memory 2.0 backfill、legacy projection 及其运维入口。
- 兰花 Excel 导入、Orchid 数据表、知识索引和销售文案维护工具。
- RAG `PolicyDecision` 兼容入口与 API `intent/template/sales_stage` 返回契约。
- Demo 功能、案例库与 cleaned 案例数据。
- `var/`、本地数据库、上传媒体和其他未跟踪运行数据。

## 完成结果

- 删除旧 Intent taxonomy 的两份数据、三份构建/迁移脚本及 Docker 复制步骤，清除已经引用不存在模块的部署残留。
- 将 34 个测试文件改为直接导入现役领域/集成模块，删除 `app/services`、`app/routers` 兼容入口及相关架构豁免。
- 删除未注册的登录页及其本地 token 链路、未使用前端 API/类型/展示函数；删除无调用的领域 service、schema、模型、包装函数、Persona 示例与静态图片。
- 将 Persona service 收敛为当前唯一使用的静态提示词加载职责；删除旧 customer-level 关键词分类链、旧 PolicyEngine 及其他无调用逻辑。
- 删除 17 个无运行时读取的 Settings 字段，清理环境模板中的退役键，并补齐购买标签、语音识别、视频理解和有赞凭据加密的现役配置。
- 将 pytest 依赖迁到 `requirements-dev.txt`，从生产依赖删除 pytest、pytest-asyncio 与未使用的 langgraph。
- 为 Nginx 补上 `/eyun/` 回调代理；将两份已退役架构/路演 HTML 移入 `docs/archive/presentations/`。
- 清除本地 `.tmp`（约 246 MB）、旧 intent embedding、pytest、Python 字节码和前端构建缓存；保留数据库、上传媒体及 `var/` 运行数据。

## 验证情况

- 仓库 308 个受 Git 管理的 Python 文件全部通过 AST 解析；应用内部导入缺失为 0，应用公开顶层定义的零引用候选为 0。
- 从 `app.main` 复查后，主应用外仅剩 8 个已在“明确保留边界”中确认的运维/回填入口。
- 前端 44 个 TS/Vue 源文件全部可达，单次出现的导出符号为 0；`vue-tsc --noEmit --noUnusedLocals --noUnusedParameters` 与生产构建均通过。
- `pytest --collect-only -q` 成功收集 571 项测试；清理直接相关测试 70 项、LLM/RAG 37 项、Persona tuning 4 项全部通过。
- 完整套件诊断到 34% 后在既有 MCP 流式 HTTP 用例长时间等待而中止；此前出现的 1 个活动发送失败和 7 个 Agent harness 失败均已在未修改的纯净 `HEAD` 副本中逐项复现，确认不是本次清理引入。
- 测试环境仍报告 Starlette/httpx 与 pydantic-settings 的两条第三方兼容性警告，本次未为消除警告而改动现役依赖栈。

# 智能销售 / AI Sales Agent

面向微信私域销售场景的智能客服系统，由 FastAPI 后端与 Vue 运营后台组成。

## 仓库结构

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

## 本地开发

后端：

```powershell
cd apps/api
python -m pip install -r requirements.txt
Copy-Item .env.example .env
uvicorn app.main:app --reload
```

前端：

```powershell
cd apps/admin
pnpm install --frozen-lockfile
pnpm dev
```

## 最小验证

```powershell
cd apps/api
python -m pytest tests/test_contracts.py tests/test_config_env.py tests/test_admin_web_deployment.py -q

cd ../admin
pnpm ts:check
pnpm build:prod
```

生产环境只有一条发布链路：提交到 GitHub `main` 后，由云服务器执行根目录的
`docker-compose.prod.yml`。Render/Vercel 不再属于项目架构。

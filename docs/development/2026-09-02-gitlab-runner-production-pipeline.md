# GitLab Runner 生产发布流水线

## 任务目标

- 使用 GitLab 共享 Runner `#4 hz-build-01`（标签 `linux`）发布 `main` 分支。
- 由 Runner 通过 VPC 内网 SSH 部署到公网地址为 `43.143.83.26` 的生产机。
- 最终更新并验证 `https://sales-agent.hzwohu.com/workbench`。
- 生产环境变量、数据库、上传文件、模型缓存和本机 Compose override 不进入 Git，也不被流水线覆盖。

## 实施计划

1. 新增 `.gitlab-ci.yml`，仅允许 `main` 分支创建生产流水线。
2. 增加生产预检阶段，验证 SSH、生产目录、GitLab remote、工作区清洁状态、环境文件和数据目录。
3. 预检通过后，在生产机调用仓库现有 `deploy/auto-deploy.sh --force`，由其 fast-forward 拉取 `origin/main` 并重建 Docker Compose 服务。
4. 部署后校验生产机 HEAD、公开健康检查和前端静态资源，确保旧资源已被替换。
5. 首次运行后查看 Pipeline 和线上手机端页面，确认发布链路可重复使用。

## 完成结果

- 已新增仅在 `main` 分支运行的 GitLab CI 配置。
- 流水线使用 `linux` 标签的共享 Runner；已确认公网生产机 `43.143.83.26` 的 VPC 地址为 `10.200.5.17`，主机名为 `VM-5-17-ubuntu`。
- VPC 目标在执行任何仓库或容器操作前，必须通过腾讯云实例元数据确认绑定公网 IP 为 `43.143.83.26`；不匹配时立即失败，避免误部署到参考案例服务器。
- 已设置生产预检、串行部署和线上验证三个阶段；目标目录、remote、生产配置或数据目录不符合预期时会在部署前失败。
- 部署阶段调用仓库现有生产脚本，不传输或覆盖生产密钥、数据库、上传文件、模型缓存和本机 Compose override。

## 验证情况

- `.gitlab-ci.yml` 已通过本地 YAML 解析。
- `git diff --check` 已执行；本次只提交 CI 配置和本开发日志。
- Pipeline `#504` 已确认共享 Runner `hz-build-01` 正常接单，但 Runner 连接 `43.143.83.26:22` 超时，部署阶段未执行，生产环境未发生变化。
- Pipeline `#506` 连接参考案例服务器 `10.200.5.12` 时在 SSH banner 阶段超时，未执行身份门禁或部署。
- 已通过现有受控运维通道核实 `43.143.83.26` 的云元数据：内网 IP 为 `10.200.5.17`，生产仓库位于 `/home/ubuntu/intelligent-customer-service`。
- 已将现有生产环境配置原样迁移到新版部署脚本要求的 `/etc/intelligent-customer-service/backend.env`，权限为 `root:docker 0640`；未输出或提交配置内容。
- 下一次 Pipeline 将先输出 Runner 公钥用于最小化配置生产 SSH 授权，再执行公网 IP 身份门禁；通过前不会重建或重启服务。

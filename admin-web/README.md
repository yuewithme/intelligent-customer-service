# Sales Agent Admin

销售 Agent 运营管理前端，由项目团队围绕私域销售流程自主设计和实现。

## 技术架构

- Vue 3 + TypeScript：组件化界面与类型约束
- Vite：本地开发和生产构建
- Vue Router：登录、体验页与销售运营页面路由
- Pinia：操作员状态管理
- Element Plus：基础交互控件
- Axios：后端 API 通信与统一鉴权
- UnoCSS + 原生 CSS：页面样式和响应式布局

## 产品模块

- 三栏销售工作台：客户列表、销售对话、客户画像与人工接管
- 销售活动管理：活动创建、发布、启停、定向发送及发送记录
- 在线体验：销售 Agent 对话体验和测试会话后台
- 策略运营入口：知识库、销售话术、意图样本和模型配置

## 目录

```text
src/
  api/          后端接口契约
  components/   项目通用组件
  config/       HTTP 客户端配置
  layouts/      销售运营后台布局
  router/       页面路由与访问控制
  store/        操作员状态
  styles/       全局视觉规范
  views/        销售业务页面
```

## 本地开发

```powershell
pnpm install
pnpm dev
```

## 验证

```powershell
pnpm ts:check
pnpm build:prod
```

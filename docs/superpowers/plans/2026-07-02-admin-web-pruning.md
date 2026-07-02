# Admin Web Pruning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a trimmed admin frontend for the intelligent customer-service system by reusing only the useful yudao-ui-admin-vue3 shell and removing unrelated platform modules before business pages are added.

**Architecture:** Create a standalone `admin-web` Vue application at the repository root. Keep the layout, router, request wrapper, shared components, Pinia, styles, and login shell; replace yudao business modules with a small static menu focused on chat logs, knowledge, templates, intents, handoff, user profiles, and model settings.

**Tech Stack:** Vue 3, Vite, TypeScript, Element Plus, Pinia, Axios, pnpm, FastAPI backend at `wechat_rag_bot`.

---

## File Structure

- Create: `admin-web/`
  - New standalone frontend copied from `yudaocode/yudao-ui-admin-vue3`, then aggressively pruned.
- Keep and modify: `admin-web/package.json`
  - Rename app, remove heavy unused dependencies, keep scripts.
- Keep and modify: `admin-web/.env`, `admin-web/.env.local`, `admin-web/.env.dev`, `admin-web/.env.prod`
  - Replace yudao title, API base URL, tenant and captcha switches.
- Keep and modify: `admin-web/src/config/axios/service.ts`
  - Simplify auth headers and response handling for current FastAPI response shape.
- Keep and modify: `admin-web/src/api/login/index.ts`
  - Replace yudao auth API with a minimal admin token flow.
- Keep and modify: `admin-web/src/store/modules/user.ts`
  - Replace yudao user-permission fetch with local admin user state.
- Keep and modify: `admin-web/src/store/modules/permission.ts`
  - Replace backend-generated routes with static intelligent-customer-service routes.
- Keep and modify: `admin-web/src/router/modules/remaining.ts`
  - Keep login, redirect, error, home.
- Delete from `admin-web/src/views/`
  - `ai`, `bpm`, `crm`, `erp`, `im`, `iot`, `mall`, `member`, `mes`, `mp`, `pay`, `report`, `system`, `wms`.
- Keep from `admin-web/src/views/`
  - `Error`, `Home`, `IFrame`, `Login`, `Profile`, `Redirect`.
- Create under `admin-web/src/views/`
  - `dashboard/index.vue`
  - `chat-logs/index.vue`
  - `chat-logs/detail.vue`
  - `knowledge/index.vue`
  - `templates/index.vue`
  - `intent-examples/index.vue`
  - `handoff/index.vue`
  - `user-profile/index.vue`
  - `model-config/index.vue`
- Create under `admin-web/src/api/`
  - `admin/chatLogs.ts`
  - `knowledge/index.ts`
  - `templates/index.ts`
  - `intentExamples/index.ts`
  - `userProfile/index.ts`
  - `handoff/index.ts`
  - `modelConfig/index.ts`

---

## Pruning Rules

The first implementation pass must delete unrelated modules before building new pages.

Keep only:

- Login and auth shell
- Admin layout
- Sidebar/menu rendering
- Top bar/tabs/breadcrumbs
- Element Plus component usage
- Axios wrapper
- Pinia stores
- Basic error pages
- Home/dashboard page
- Shared utilities needed by the kept shell

Remove immediately:

- Mall, CRM, ERP, MES, WMS
- BPM and Flowable designer
- Payment
- IoT
- MP/official-account modules
- IM module
- Member center
- Report/data-screen module
- Yudao system management pages unless reintroduced later through our own API
- Tenant UI and tenant package management
- Social login
- Captcha
- Yudao docs alert
- Baidu analytics/map config
- API encryption defaults

Do not implement RBAC in the first pass. Use a static admin menu and a single admin token.

---

### Task 1: Create The Trimmed Frontend Workspace

**Files:**
- Create: `admin-web/`
- Modify: `admin-web/package.json`
- Modify: `admin-web/.env`
- Modify: `admin-web/.env.local`

- [ ] **Step 1: Copy yudao frontend into `admin-web`**

Run:

```powershell
git clone --depth 1 https://github.com/yudaocode/yudao-ui-admin-vue3.git admin-web
Remove-Item -LiteralPath admin-web\.git -Recurse -Force
```

Expected:

```text
admin-web/src
admin-web/package.json
admin-web/vite.config.ts
```

- [ ] **Step 2: Rename package and app metadata**

Edit `admin-web/package.json`:

```json
{
  "name": "intelligent-customer-service-admin",
  "version": "0.1.0",
  "description": "Admin web for intelligent customer-service system",
  "private": true
}
```

Keep the existing `scripts`, `dependencies`, `devDependencies`, `engines`, and `license` fields during this step.

- [ ] **Step 3: Replace base env values**

Edit `admin-web/.env`:

```dotenv
VITE_APP_TITLE=智能客服后台
VITE_PORT=5173
VITE_OPEN=true
VITE_APP_TENANT_ENABLE=false
VITE_APP_CAPTCHA_ENABLE=false
VITE_APP_DOCALERT_ENABLE=false
VITE_APP_API_ENCRYPT_ENABLE=false
```

Edit `admin-web/.env.local`:

```dotenv
NODE_ENV=development
VITE_DEV=true
VITE_BASE_URL='http://127.0.0.1:8000'
VITE_API_URL=
VITE_DROP_DEBUGGER=false
VITE_DROP_CONSOLE=false
VITE_SOURCEMAP=false
VITE_COMPRESS=none
VITE_BASE_PATH=/
```

- [ ] **Step 4: Install and verify the unpruned app still starts**

Run:

```powershell
cd admin-web
pnpm install
pnpm dev --host 127.0.0.1
```

Expected: Vite starts on `http://127.0.0.1:5173` or the next available port.

---

### Task 2: Delete Unrelated Business Modules

**Files:**
- Delete: `admin-web/src/views/ai`
- Delete: `admin-web/src/views/bpm`
- Delete: `admin-web/src/views/crm`
- Delete: `admin-web/src/views/erp`
- Delete: `admin-web/src/views/im`
- Delete: `admin-web/src/views/iot`
- Delete: `admin-web/src/views/mall`
- Delete: `admin-web/src/views/member`
- Delete: `admin-web/src/views/mes`
- Delete: `admin-web/src/views/mp`
- Delete: `admin-web/src/views/pay`
- Delete: `admin-web/src/views/report`
- Delete: `admin-web/src/views/system`
- Delete: `admin-web/src/views/wms`
- Delete matching folders under `admin-web/src/api/`

- [ ] **Step 1: Remove business view folders**

Run:

```powershell
$viewFolders = @('ai','bpm','crm','erp','im','iot','mall','member','mes','mp','pay','report','system','wms')
foreach ($folder in $viewFolders) {
  $path = Join-Path 'admin-web/src/views' $folder
  if (Test-Path $path) { Remove-Item -LiteralPath $path -Recurse -Force }
}
```

Expected: only shell and future admin folders remain under `admin-web/src/views`.

- [ ] **Step 2: Remove matching API folders**

Run:

```powershell
$apiFolders = @('ai','bpm','crm','erp','im','iot','mall','member','mes','mp','pay','system','wms')
foreach ($folder in $apiFolders) {
  $path = Join-Path 'admin-web/src/api' $folder
  if (Test-Path $path) { Remove-Item -LiteralPath $path -Recurse -Force }
}
```

Expected: no yudao business API folders remain.

- [ ] **Step 3: Remove heavy dependencies only used by deleted modules**

Edit `admin-web/package.json` and remove these dependencies when no kept file imports them:

```json
[
  "@form-create/designer",
  "@form-create/element-ui",
  "@microsoft/fetch-event-source",
  "@videojs-player/vue",
  "@wangeditor-next/editor",
  "@wangeditor-next/editor-for-vue",
  "@wangeditor-next/plugin-mention",
  "benz-amr-recorder",
  "bpmn-js-token-simulation",
  "camunda-bpmn-moddle",
  "dhtmlx-gantt",
  "diagram-js",
  "echarts-wordcloud",
  "fast-xml-parser",
  "jsbarcode",
  "jsoneditor",
  "livekit-client",
  "markdown-it",
  "markmap-common",
  "markmap-lib",
  "markmap-toolbar",
  "markmap-view",
  "min-dash",
  "qrcode",
  "snabbdom",
  "steady-xml",
  "video.js",
  "vue3-print-nb",
  "vue3-signature",
  "vuedraggable",
  "xml-js"
]
```

Run:

```powershell
cd admin-web
pnpm install
pnpm lint:eslint:check
```

Expected: any missing import points to a kept shell file that still depends on a deleted feature. Either remove that shell reference or restore the minimal dependency if it is truly needed.

---

### Task 3: Replace Dynamic Yudao Menus With Static Admin Routes

**Files:**
- Modify: `admin-web/src/store/modules/permission.ts`
- Create: `admin-web/src/router/modules/admin.ts`
- Modify: `admin-web/src/router/index.ts`

- [ ] **Step 1: Create static admin route module**

Create `admin-web/src/router/modules/admin.ts`:

```ts
import { Layout } from '@/utils/routerHelper'

const adminRoutes: AppRouteRecordRaw[] = [
  {
    path: '/',
    component: Layout,
    redirect: '/dashboard',
    name: 'Root',
    meta: { title: '首页', icon: 'ep:home-filled', alwaysShow: false },
    children: [
      {
        path: 'dashboard',
        component: () => import('@/views/dashboard/index.vue'),
        name: 'Dashboard',
        meta: { title: '工作台', icon: 'ep:data-analysis', noCache: true }
      }
    ]
  },
  {
    path: '/operations',
    component: Layout,
    name: 'Operations',
    meta: { title: '客服运营', icon: 'ep:service', alwaysShow: true },
    children: [
      {
        path: 'chat-logs',
        component: () => import('@/views/chat-logs/index.vue'),
        name: 'ChatLogs',
        meta: { title: '对话日志', icon: 'ep:chat-dot-round' }
      },
      {
        path: 'handoff',
        component: () => import('@/views/handoff/index.vue'),
        name: 'Handoff',
        meta: { title: '人工转接', icon: 'ep:phone' }
      },
      {
        path: 'user-profile',
        component: () => import('@/views/user-profile/index.vue'),
        name: 'UserProfile',
        meta: { title: '用户画像', icon: 'ep:user' }
      }
    ]
  },
  {
    path: '/knowledge-ops',
    component: Layout,
    name: 'KnowledgeOps',
    meta: { title: '知识运营', icon: 'ep:collection', alwaysShow: true },
    children: [
      {
        path: 'knowledge',
        component: () => import('@/views/knowledge/index.vue'),
        name: 'Knowledge',
        meta: { title: '知识库', icon: 'ep:document' }
      },
      {
        path: 'templates',
        component: () => import('@/views/templates/index.vue'),
        name: 'Templates',
        meta: { title: '话术模板', icon: 'ep:tickets' }
      },
      {
        path: 'intent-examples',
        component: () => import('@/views/intent-examples/index.vue'),
        name: 'IntentExamples',
        meta: { title: '意图样本', icon: 'ep:connection' }
      }
    ]
  },
  {
    path: '/settings',
    component: Layout,
    name: 'Settings',
    meta: { title: '系统设置', icon: 'ep:setting', alwaysShow: true },
    children: [
      {
        path: 'model-config',
        component: () => import('@/views/model-config/index.vue'),
        name: 'ModelConfig',
        meta: { title: '模型配置', icon: 'ep:cpu' }
      }
    ]
  }
]

export default adminRoutes
```

- [ ] **Step 2: Make permission store use static routes**

Replace `generateRoutes()` in `admin-web/src/store/modules/permission.ts` with:

```ts
import adminRoutes from '@/router/modules/admin'

async generateRoutes(): Promise<unknown> {
  return new Promise<void>((resolve) => {
    this.addRouters = adminRoutes.concat([
      {
        path: '/:path(.*)*',
        component: () => import('@/views/Error/404.vue'),
        name: '404Page',
        meta: {
          hidden: true,
          breadcrumb: false
        }
      }
    ])
    this.routers = cloneDeep(remainingRouter).concat(adminRoutes)
    resolve()
  })
}
```

Keep imports for `cloneDeep` and `remainingRouter`. Remove imports for `generateRoute`, `flatMultiLevelRoutes`, `CACHE_KEY`, and `useCache` if no longer used.

- [ ] **Step 3: Verify no yudao menu API remains in routing**

Run:

```powershell
rg "ROLE_ROUTERS|generateRoute|get-permission-info|menus" admin-web/src
```

Expected: no route generation depends on backend `menus`.

---

### Task 4: Simplify Authentication For Current Backend

**Files:**
- Modify: `admin-web/src/api/login/index.ts`
- Modify: `admin-web/src/store/modules/user.ts`
- Modify: `admin-web/src/config/axios/service.ts`
- Modify: `admin-web/src/utils/auth.ts`

- [ ] **Step 1: Replace yudao login API**

Replace `admin-web/src/api/login/index.ts` with:

```ts
import request from '@/config/axios'

export interface AdminLoginVO {
  token: string
}

export const login = (data: AdminLoginVO) => {
  return Promise.resolve({
    code: 0,
    message: 'success',
    data: {
      accessToken: data.token,
      refreshToken: '',
      expiresTime: Date.now() + 1000 * 60 * 60 * 24 * 30
    }
  })
}

export const loginOut = () => {
  return Promise.resolve({ code: 0, message: 'success', data: null })
}

export const getInfo = () => {
  return Promise.resolve({
    permissions: ['*:*:*'],
    roles: ['admin'],
    user: {
      id: 1,
      avatar: '',
      nickname: '管理员',
      deptId: 0
    },
    menus: []
  })
}

export const checkAdminToken = () => {
  return request.get({ url: '/health' })
}
```

- [ ] **Step 2: Simplify Axios response handling**

In `admin-web/src/config/axios/service.ts`, keep:

```ts
const resultCode = data.code ?? 0
const msg = data.message || data.msg || '请求失败'

if (resultCode !== 0 && resultCode !== 200) {
  ElNotification.error({ title: msg })
  return Promise.reject(new Error(msg))
}

return data
```

Remove refresh-token replay logic in this first pass. The current backend does not issue refresh tokens.

- [ ] **Step 3: Verify login shell works with API key**

Run:

```powershell
cd admin-web
pnpm dev --host 127.0.0.1
```

Expected: entering the backend API key stores a bearer token and opens the admin layout.

---

### Task 5: Add Empty Business Pages After Pruning

**Files:**
- Create: `admin-web/src/views/dashboard/index.vue`
- Create: `admin-web/src/views/chat-logs/index.vue`
- Create: `admin-web/src/views/chat-logs/detail.vue`
- Create: `admin-web/src/views/knowledge/index.vue`
- Create: `admin-web/src/views/templates/index.vue`
- Create: `admin-web/src/views/intent-examples/index.vue`
- Create: `admin-web/src/views/handoff/index.vue`
- Create: `admin-web/src/views/user-profile/index.vue`
- Create: `admin-web/src/views/model-config/index.vue`

- [ ] **Step 1: Create a minimal reusable page skeleton**

For each page, use this structure and change only the page title:

```vue
<template>
  <ContentWrap>
    <div class="page-header">
      <h2>{{ title }}</h2>
    </div>
    <ElEmpty description="页面建设中" />
  </ContentWrap>
</template>

<script setup lang="ts">
const title = '对话日志'
</script>

<style scoped>
.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}

.page-header h2 {
  margin: 0;
  font-size: 18px;
  font-weight: 600;
}
</style>
```

Use these titles:

```text
工作台
对话日志
对话详情
知识库
话术模板
意图样本
人工转接
用户画像
模型配置
```

- [ ] **Step 2: Verify static routes compile**

Run:

```powershell
cd admin-web
pnpm ts:check
pnpm build:local
```

Expected: type-check and local build pass without references to deleted yudao modules.

---

### Task 6: Connect The First Real Page, Chat Logs

**Files:**
- Create: `admin-web/src/api/admin/chatLogs.ts`
- Modify: `admin-web/src/views/chat-logs/index.vue`
- Modify: `admin-web/src/views/chat-logs/detail.vue`

- [ ] **Step 1: Add chat log API client**

Create `admin-web/src/api/admin/chatLogs.ts`:

```ts
import request from '@/config/axios'

export interface ChatLogQuery {
  page: number
  page_size: number
  user_id?: string
  session_id?: string
  route?: string
  primary_intent?: string
  template_id?: string
  status?: string
  need_human?: boolean
  keyword?: string
  start_time?: string
  end_time?: string
}

export const getChatLogs = (params: ChatLogQuery) => {
  return request.get({ url: '/api/v1/admin/chat-logs', params })
}

export const getChatLogDetail = (traceId: string) => {
  return request.get({ url: `/api/v1/admin/chat-logs/${traceId}` })
}

export const getChatLogStats = (params?: { start_time?: string; end_time?: string }) => {
  return request.get({ url: '/api/v1/admin/chat-log-stats', params })
}
```

- [ ] **Step 2: Implement list page using existing backend contract**

The list page must include filters for `keyword`, `route`, `need_human`, `user_id`, and `session_id`, plus pagination.

Success check:

```powershell
cd wechat_rag_bot
uvicorn app.main:app --reload
```

Then:

```powershell
cd admin-web
pnpm dev --host 127.0.0.1
```

Expected: `/operations/chat-logs` loads data from `http://127.0.0.1:8000/api/v1/admin/chat-logs`.

---

## Verification

Run the narrowest checks first:

```powershell
cd admin-web
pnpm ts:check
pnpm lint:eslint:check
pnpm build:local
```

Run backend smoke check:

```powershell
cd wechat_rag_bot
python -m pytest tests/test_chat_logs.py -q
```

Manual browser checks:

- Login page opens.
- API key can be saved as bearer token.
- Sidebar contains only:
  - 工作台
  - 客服运营
  - 对话日志
  - 人工转接
  - 用户画像
  - 知识运营
  - 知识库
  - 话术模板
  - 意图样本
  - 系统设置
  - 模型配置
- No menu item references mall, CRM, ERP, BPM, payment, IoT, member, report, WMS, MES, MP, or IM.
- `rg "mall|crm|erp|bpm|pay|iot|member|wms|mes|mp|Flowable|租户套餐" admin-web/src` only returns false positives from comments or no results.

---

## Execution Choice

Plan complete. Recommended execution order:

1. Do Tasks 1-5 as one pruning milestone.
2. Verify the trimmed shell starts and builds.
3. Do Task 6 as the first real business page.
4. Add knowledge, templates, intents, handoff, user profile, and model config only after chat logs is working.

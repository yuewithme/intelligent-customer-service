# Full Admin Workbench From Yudao Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Start from `yudaocode/yudao-ui-admin-vue3`, prune it into a minimal intelligent-customer-service admin frontend, and build an IM-style supervised handoff workbench connected to the existing FastAPI backend.

**Architecture:** Use yudao only as a Vue admin shell: layout, Element Plus, routing, Pinia, request wrapper, and basic login. Remove unrelated business modules first, then connect the backend conversation APIs and implement a three-column workbench where AI replies by default, humans monitor by default, and humans can reply only after handoff and claim.

**Tech Stack:** Vue 3, Vite, TypeScript, Element Plus, Pinia, Axios, pnpm, FastAPI, SQLAlchemy, SQLite, pytest.

---

## One-Shot Prompt For AI Development

Use this prompt if assigning the whole job to another AI:

```text
你是一个资深全栈工程师。请在 D:/Codex产品开发/智能客服 中完成智能客服后台管理平台开发。

最终目标：
从 GitHub 开源项目 https://github.com/yudaocode/yudao-ui-admin-vue3 开始，裁剪成我们的智能客服后台，不要保留芋道的大量无关业务模块。后台第一屏要做成类似 IM 的“客服监督与受控接管工作台”：AI 默认回复客户，人工可以查看和监控聊天记录，但默认不能发送消息；只有 AI 触发转人工，或有权限人员强制转人工后，客服领取会话，才允许人工回复。

核心规则：
1. AI 默认拥有回复权。
2. 人工默认只能查看会话，不能回复。
3. ai_active / ai_waiting 状态下，前端不显示输入框，后端也必须拒绝人工回复。
4. handoff_pending 状态下，只能领取接管，不能直接回复。
5. claim 成功后，会话进入 human_active。
6. 只有 human_active 且当前 operator_id 是 owner_id 时，才允许 reply。
7. resolved 状态只能查看。
8. 权限限制必须由后端强制校验，不能只靠前端隐藏按钮。

现有项目：
- 后端位于 D:/Codex产品开发/智能客服/wechat_rag_bot
- 当前是 FastAPI 项目
- 已有 /api/v1/chat
- 已有 /wechat/callback
- 已有 /api/v1/admin/chat-logs
- 已有知识库、话术、意图、用户画像等接口

第一阶段：搭建和裁剪前端
1. 克隆 yudaocode/yudao-ui-admin-vue3 到仓库根目录 admin-web。
2. 删除 admin-web/.git。
3. 重命名 package 为 intelligent-customer-service-admin。
4. 修改环境变量：
   - VITE_APP_TITLE=智能客服后台
   - VITE_PORT=5173
   - VITE_BASE_URL=http://127.0.0.1:8000
   - VITE_API_URL=
   - VITE_APP_TENANT_ENABLE=false
   - VITE_APP_CAPTCHA_ENABLE=false
   - VITE_APP_DOCALERT_ENABLE=false
   - VITE_APP_API_ENCRYPT_ENABLE=false
5. 立即删除无关模块：
   - src/views/ai
   - src/views/bpm
   - src/views/crm
   - src/views/erp
   - src/views/im
   - src/views/iot
   - src/views/mall
   - src/views/member
   - src/views/mes
   - src/views/mp
   - src/views/pay
   - src/views/report
   - src/views/system
   - src/views/wms
   - src/api 下对应业务目录
6. 不要保留商城、ERP、CRM、工作流、支付、IoT、会员、报表、租户套餐等功能。
7. 只保留登录、Layout、侧边栏、路由、Pinia、Axios、Element Plus、基础样式、Error/Home/Profile/Redirect。

第二阶段：调整前端基础能力
1. 使用静态菜单，不接芋道后端动态菜单。
2. 简化登录：MVP 使用管理员 API Key / token 存储到本地，后续请求统一带 Authorization: Bearer <token>。
3. 简化 Axios：
   - 成功结构兼容 { code: 0, message: "success", data: ... }
   - 不实现 refresh-token
   - 不实现 tenant-id
   - 不实现 API 加密
4. 新建静态路由：
   - /workbench 客服工作台
   - /operations/chat-logs 对话日志
   - /operations/handoff 人工转接
   - /operations/user-profile 用户画像
   - /knowledge-ops/knowledge 知识库
   - /knowledge-ops/templates 话术模板
   - /knowledge-ops/intent-examples 意图样本
   - /settings/model-config 模型配置

第三阶段：后端会话接口
先检查后端是否已经有这些文件和接口。如果已有，不要重复创建，先阅读现有实现并补缺口：
- app/services/conversation_service.py
- app/routers/admin_conversations.py
- app/schemas/conversation.py
- tests/test_admin_conversations.py

必须提供这些接口：
- GET /api/v1/admin/conversations
- GET /api/v1/admin/conversations/{conversation_id}
- POST /api/v1/admin/conversations/{conversation_id}/claim
- POST /api/v1/admin/conversations/{conversation_id}/reply
- POST /api/v1/admin/conversations/{conversation_id}/force-handoff
- POST /api/v1/admin/conversations/{conversation_id}/release-to-ai
- POST /api/v1/admin/conversations/{conversation_id}/resolve

后端模型需要支持：
- ConversationModel
- ConversationMessageModel

会话字段至少包含：
- conversation_id
- channel
- user_id
- session_id
- tenant_id
- status
- owner_id
- last_message
- last_route
- last_intent
- handoff_reason
- handoff_ticket_id
- unread_count
- created_at
- updated_at

消息字段至少包含：
- conversation_id
- trace_id
- message_id
- sender_type: customer | ai | human | system
- sender_id
- content
- route
- primary_intent
- metadata_json
- created_at

第四阶段：把 /api/v1/chat 接入会话时间线
1. /api/v1/chat 成功后自动 upsert conversation。
2. 写入 customer 消息。
3. 如果 AI 有 answer，写入 ai 消息。
4. 如果 need_human=true，conversation.status=handoff_pending。
5. 否则 conversation.status=ai_waiting。
6. 不要破坏现有 chat log 逻辑。

第五阶段：前端客服工作台
创建：
- admin-web/src/api/admin/conversations.ts
- admin-web/src/views/workbench/index.vue
- admin-web/src/views/workbench/components/ConversationList.vue
- admin-web/src/views/workbench/components/MessagePanel.vue
- admin-web/src/views/workbench/components/SupervisionPanel.vue
- admin-web/src/views/workbench/components/ReplyComposer.vue

工作台布局：
- 左侧：会话列表，支持状态筛选、关键词搜索、未读/状态显示
- 中间：消息时间线，区分 customer / ai / human
- 右侧：监督面板，显示状态、用户、意图、路由、转人工原因、操作按钮

ReplyComposer 必须按状态控制：
- ai_active / ai_waiting：显示“当前由 AI 自动回复，人工仅可监控”，没有输入框
- handoff_pending：显示“领取接管”
- human_active：显示输入框和发送按钮
- resolved：显示“会话已结束，仅可查看”

第六阶段：测试和验证
后端至少运行：
cd D:/Codex产品开发/智能客服/wechat_rag_bot
python -m pytest tests/test_admin_conversations.py tests/test_wechat.py tests/test_chat_logs.py tests/test_handoff_fallbacks.py tests/test_chat_api.py -q

前端至少运行：
cd D:/Codex产品开发/智能客服/admin-web
pnpm ts:check
pnpm build:local

开发要求：
1. 使用 TDD：新增后端能力先写失败测试，再实现。
2. 保持改动最小。
3. 不要做无关重构。
4. 不要引入完整 IM 系统。
5. 不要实现复杂 RBAC、多租户、排班、客服自动分配，除非另有明确要求。
6. 不要让人工绕过转人工规则直接回复。
7. 不要破坏微信回调和 /api/v1/chat 现有行为。
8. 如果发现工作树已有用户改动，不要回退，必须绕开或兼容。

交付说明必须包含：
- 完成了哪些后端接口
- 完成了哪些前端页面
- 修改了哪些关键文件
- 哪些测试通过
- 仍未完成的能力，例如“人工回复真实出站发送到微信/企微”
```

---

## Current Repository Assumptions

- Backend exists at `wechat_rag_bot/`.
- Frontend admin shell may not exist yet. If `admin-web/` does not exist, create it from yudao.
- Some backend supervised handoff APIs may already exist. Inspect before creating duplicates.
- The first production-grade milestone does not need real human outbound delivery to WeChat. Human reply may first write to the internal conversation timeline. Real WeChat/Enterprise WeChat outbound delivery is a later milestone.

---

## Phase 1: Create `admin-web` From Yudao

**Files:**
- Create: `admin-web/`
- Modify: `admin-web/package.json`
- Modify: `admin-web/.env`
- Modify: `admin-web/.env.local`

- [ ] **Step 1: Clone yudao**

Run from repository root:

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

- [ ] **Step 2: Rename frontend package**

Set these top-level fields in `admin-web/package.json`:

```json
{
  "name": "intelligent-customer-service-admin",
  "version": "0.1.0",
  "description": "Admin web for intelligent customer-service system",
  "private": true
}
```

Keep scripts and dependencies for now.

- [ ] **Step 3: Replace environment settings**

Set `admin-web/.env`:

```dotenv
VITE_APP_TITLE=智能客服后台
VITE_PORT=5173
VITE_OPEN=true
VITE_APP_TENANT_ENABLE=false
VITE_APP_CAPTCHA_ENABLE=false
VITE_APP_DOCALERT_ENABLE=false
VITE_APP_API_ENCRYPT_ENABLE=false
```

Set `admin-web/.env.local`:

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

- [ ] **Step 4: Install once**

```powershell
cd admin-web
pnpm install
```

Expected: dependencies install successfully.

---

## Phase 2: Prune Yudao Down To A Shell

**Delete immediately:**

```text
admin-web/src/views/ai
admin-web/src/views/bpm
admin-web/src/views/crm
admin-web/src/views/erp
admin-web/src/views/im
admin-web/src/views/iot
admin-web/src/views/mall
admin-web/src/views/member
admin-web/src/views/mes
admin-web/src/views/mp
admin-web/src/views/pay
admin-web/src/views/report
admin-web/src/views/system
admin-web/src/views/wms
```

**Also delete matching `admin-web/src/api/*` folders:**

```text
ai
bpm
crm
erp
im
iot
mall
member
mes
mp
pay
system
wms
```

- [ ] **Step 1: Delete irrelevant folders**

```powershell
$viewFolders = @('ai','bpm','crm','erp','im','iot','mall','member','mes','mp','pay','report','system','wms')
foreach ($folder in $viewFolders) {
  $path = Join-Path 'admin-web/src/views' $folder
  if (Test-Path $path) { Remove-Item -LiteralPath $path -Recurse -Force }
}

$apiFolders = @('ai','bpm','crm','erp','im','iot','mall','member','mes','mp','pay','system','wms')
foreach ($folder in $apiFolders) {
  $path = Join-Path 'admin-web/src/api' $folder
  if (Test-Path $path) { Remove-Item -LiteralPath $path -Recurse -Force }
}
```

- [ ] **Step 2: Keep only shell folders**

Keep:

```text
src/layout
src/router
src/store
src/config/axios
src/components
src/hooks
src/utils
src/styles
src/views/Login
src/views/Home
src/views/Error
src/views/Profile
src/views/Redirect
src/views/IFrame
```

- [ ] **Step 3: Verify there are no visible yudao business routes**

```powershell
rg "mall|crm|erp|bpm|pay|iot|member|wms|mes|mp|Flowable|租户套餐" admin-web/src
```

Expected: no active route/menu/page references. Comments are acceptable only if they are not compiled or user-visible.

---

## Phase 3: Simplify Frontend Auth And Routing

**Files:**
- Modify: `admin-web/src/api/login/index.ts`
- Modify: `admin-web/src/config/axios/service.ts`
- Modify: `admin-web/src/store/modules/user.ts`
- Modify: `admin-web/src/store/modules/permission.ts`
- Create: `admin-web/src/router/modules/admin.ts`

- [ ] **Step 1: Replace yudao auth with local admin token**

`admin-web/src/api/login/index.ts` should expose:

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

- [ ] **Step 2: Simplify Axios**

Keep only:

```text
baseURL
Authorization: Bearer token
code/message/data response handling
401 handling
generic error notification
```

Remove:

```text
refresh-token replay
tenant-id
visit-tenant-id
captcha assumptions
API encryption
doc alert behavior
```

- [ ] **Step 3: Add static route module**

Create `admin-web/src/router/modules/admin.ts`:

```ts
import { Layout } from '@/utils/routerHelper'

const adminRoutes: AppRouteRecordRaw[] = [
  {
    path: '/workbench',
    component: Layout,
    name: 'WorkbenchRoot',
    meta: { title: '客服工作台', icon: 'ep:service' },
    children: [
      {
        path: '',
        component: () => import('@/views/workbench/index.vue'),
        name: 'Workbench',
        meta: { title: '客服工作台', icon: 'ep:service', noCache: true }
      }
    ]
  },
  {
    path: '/operations',
    component: Layout,
    name: 'Operations',
    meta: { title: '客服运营', icon: 'ep:operation', alwaysShow: true },
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

- [ ] **Step 4: Use static routes in permission store**

`admin-web/src/store/modules/permission.ts` should use `adminRoutes`, not backend `menus`.

Expected:

```text
No dependency on /system/auth/get-permission-info for route generation.
No dependency on roleRouters for MVP route generation.
```

---

## Phase 4: Backend Conversation APIs

If these already exist, inspect and only patch missing behavior.

**Files:**
- Modify: `wechat_rag_bot/app/db/models.py`
- Create/modify: `wechat_rag_bot/app/schemas/conversation.py`
- Create/modify: `wechat_rag_bot/app/services/conversation_service.py`
- Create/modify: `wechat_rag_bot/app/routers/admin_conversations.py`
- Modify: `wechat_rag_bot/app/main.py`
- Modify: `wechat_rag_bot/app/services/chat_orchestrator.py`
- Test: `wechat_rag_bot/tests/test_admin_conversations.py`

- [ ] **Step 1: Add tests first**

Tests must cover:

```text
GET /api/v1/admin/conversations starts empty
/api/v1/chat creates a conversation
normal AI conversation rejects human reply
handoff conversation can be claimed
claimed conversation accepts human reply
force handoff converts AI conversation to handoff_pending
release-to-ai moves human_active back to ai_active
resolve moves conversation to resolved
auth is required when API_AUTH_ENABLED=true
```

- [ ] **Step 2: Add/verify models**

Add `ConversationModel` and `ConversationMessageModel` in `app/db/models.py`.

- [ ] **Step 3: Add/verify service**

`conversation_service.py` must enforce:

```text
claim only from handoff_pending
reply only from human_active and matching owner_id
release-to-ai only from human_active and matching owner_id
resolved cannot be force-handoffed
```

- [ ] **Step 4: Add/verify router**

Register:

```python
app.include_router(admin_conversations.router)
```

- [ ] **Step 5: Persist chat turns**

In `handle_chat()`, after result is built:

```python
await record_ai_turn(message=message, result=result)
```

Do not remove `record_chat_log()`.

---

## Phase 5: Frontend Workbench API Client

**Files:**
- Create: `admin-web/src/api/admin/conversations.ts`

- [ ] **Step 1: Add client**

```ts
import request from '@/config/axios'

export type ConversationStatus =
  | 'ai_active'
  | 'ai_waiting'
  | 'handoff_pending'
  | 'human_active'
  | 'resolved'

export interface ConversationItem {
  conversation_id: string
  channel: string
  user_id: string
  session_id?: string | null
  tenant_id: string
  status: ConversationStatus
  owner_id?: string | null
  last_message?: string | null
  last_route?: string | null
  last_intent?: string | null
  handoff_reason?: string | null
  handoff_ticket_id?: string | null
  unread_count: number
  created_at: string
  updated_at: string
}

export interface ConversationMessage {
  id: number
  conversation_id: string
  trace_id?: string | null
  message_id?: string | null
  sender_type: 'customer' | 'ai' | 'human' | 'system'
  sender_id?: string | null
  content: string
  route?: string | null
  primary_intent?: string | null
  metadata: Record<string, unknown>
  created_at: string
}

export const getConversations = (params: {
  page: number
  page_size: number
  status?: string
  owner_id?: string
  keyword?: string
}) => request.get({ url: '/api/v1/admin/conversations', params })

export const getConversationDetail = (conversationId: string) =>
  request.get({ url: `/api/v1/admin/conversations/${conversationId}` })

export const claimConversation = (conversationId: string, operator_id: string) =>
  request.post({ url: `/api/v1/admin/conversations/${conversationId}/claim`, data: { operator_id } })

export const replyConversation = (conversationId: string, operator_id: string, content: string) =>
  request.post({ url: `/api/v1/admin/conversations/${conversationId}/reply`, data: { operator_id, content } })

export const forceHandoff = (conversationId: string, operator_id: string, reason: string) =>
  request.post({ url: `/api/v1/admin/conversations/${conversationId}/force-handoff`, data: { operator_id, reason } })

export const releaseToAi = (conversationId: string, operator_id: string) =>
  request.post({ url: `/api/v1/admin/conversations/${conversationId}/release-to-ai`, data: { operator_id } })

export const resolveConversation = (conversationId: string, operator_id: string, reason?: string) =>
  request.post({ url: `/api/v1/admin/conversations/${conversationId}/resolve`, data: { operator_id, reason } })
```

---

## Phase 6: Frontend Workbench UI

**Files:**
- Create: `admin-web/src/views/workbench/index.vue`
- Create: `admin-web/src/views/workbench/components/ConversationList.vue`
- Create: `admin-web/src/views/workbench/components/MessagePanel.vue`
- Create: `admin-web/src/views/workbench/components/SupervisionPanel.vue`
- Create: `admin-web/src/views/workbench/components/ReplyComposer.vue`

- [ ] **Step 1: Create three-column layout**

`index.vue`:

```vue
<template>
  <div class="workbench">
    <ConversationList class="panel list" @select="selectConversation" />
    <MessagePanel class="panel messages" :conversation-id="selectedId" />
    <SupervisionPanel class="panel side" :conversation-id="selectedId" />
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import ConversationList from './components/ConversationList.vue'
import MessagePanel from './components/MessagePanel.vue'
import SupervisionPanel from './components/SupervisionPanel.vue'

const selectedId = ref('')
const selectConversation = (id: string) => {
  selectedId.value = id
}
</script>

<style scoped>
.workbench {
  display: grid;
  grid-template-columns: 320px minmax(420px, 1fr) 360px;
  gap: 12px;
  height: calc(100vh - 96px);
  padding: 12px;
  background: #f5f7fb;
}

.panel {
  min-height: 0;
  overflow: hidden;
  background: #fff;
  border: 1px solid #e5e7eb;
  border-radius: 6px;
}
</style>
```

- [ ] **Step 2: ConversationList requirements**

Must include:

```text
status filter
keyword search
conversation list
last message preview
status tag
unread count
select event with conversation_id
refresh button
```

- [ ] **Step 3: MessagePanel requirements**

Must include:

```text
load detail by conversation_id
render message bubbles by sender_type
customer left side
ai and human visually distinct
empty state when no conversation selected
manual refresh
```

- [ ] **Step 4: SupervisionPanel requirements**

Must include:

```text
conversation status
channel
user_id
session_id
last_route
last_intent
handoff_reason
claim button for handoff_pending
force handoff button for ai_active/ai_waiting
release to AI for human_active
resolve button
ReplyComposer
```

- [ ] **Step 5: ReplyComposer permission gate**

`ReplyComposer.vue` must include the exact behavior:

```vue
<template>
  <div class="composer">
    <ElAlert
      v-if="status === 'ai_active' || status === 'ai_waiting'"
      title="当前由 AI 自动回复，人工仅可监控"
      type="info"
      :closable="false"
    />
    <ElButton v-else-if="status === 'handoff_pending'" type="primary" @click="$emit('claim')">
      领取接管
    </ElButton>
    <template v-else-if="status === 'human_active'">
      <ElInput v-model="content" type="textarea" :rows="3" placeholder="输入人工回复" />
      <ElButton type="primary" :disabled="!content.trim()" @click="send">发送</ElButton>
    </template>
    <ElAlert v-else title="会话已结束，仅可查看" type="warning" :closable="false" />
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'

defineProps<{ status: string }>()
const emit = defineEmits<{ claim: []; send: [content: string] }>()
const content = ref('')
const send = () => {
  const value = content.value.trim()
  if (!value) return
  emit('send', value)
  content.value = ''
}
</script>
```

---

## Phase 7: Placeholder Admin Pages

Create lightweight placeholders so routes build:

```text
admin-web/src/views/chat-logs/index.vue
admin-web/src/views/handoff/index.vue
admin-web/src/views/user-profile/index.vue
admin-web/src/views/knowledge/index.vue
admin-web/src/views/templates/index.vue
admin-web/src/views/intent-examples/index.vue
admin-web/src/views/model-config/index.vue
```

Each may initially be:

```vue
<template>
  <ContentWrap>
    <ElEmpty description="页面建设中" />
  </ContentWrap>
</template>
```

---

## Verification

Backend:

```powershell
cd D:/Codex产品开发/智能客服/wechat_rag_bot
python -m pytest tests/test_admin_conversations.py tests/test_wechat.py tests/test_chat_logs.py tests/test_handoff_fallbacks.py tests/test_chat_api.py -q
```

Frontend:

```powershell
cd D:/Codex产品开发/智能客服/admin-web
pnpm ts:check
pnpm build:local
```

Manual smoke test:

```text
1. Start backend.
2. Start admin-web.
3. Login with API key.
4. Send normal /api/v1/chat message.
5. Confirm workbench shows conversation as ai_waiting and no reply input.
6. Send /api/v1/chat message "我要转人工".
7. Confirm workbench shows handoff_pending.
8. Claim conversation.
9. Confirm status becomes human_active and input appears.
10. Send human reply.
11. Confirm human message appears in timeline.
12. Release to AI.
13. Confirm status becomes ai_active.
14. Resolve conversation.
15. Confirm conversation is read-only.
```

---

## Non-Goals For This Milestone

Do not implement:

```text
full RBAC
multi-tenant packages
customer service scheduling
automatic staff assignment
real outbound human reply delivery to WeChat/Enterprise WeChat
CRM/ERP/Mall/BPM/Payment/IoT
full data dashboard
AI quality scoring
knowledge auto-ingestion from human replies
```

These are later milestones.

---

## Completion Criteria

The milestone is complete when:

```text
admin-web exists and runs
yudao business modules are removed
static intelligent-customer-service menu works
workbench route opens
conversation APIs are connected
normal AI conversation is read-only to humans
handoff conversation can be claimed
only claimed human_active conversation can be replied to
backend rejects illegal replies
backend and frontend verification commands pass
```

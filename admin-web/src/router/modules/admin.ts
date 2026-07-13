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
        path: 'current-activities',
        component: () => import('@/views/current-activities/index.vue'),
        name: 'CurrentActivities',
        meta: { title: '目前活动', icon: 'ep:promotion' }
      },
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

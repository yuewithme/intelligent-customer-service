import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import SalesLayout from '@/layouts/SalesLayout.vue'
import { clearGateRole, setGateRole, type GateRole } from '@/utils/gate'

const PlaceholderPage = () => import('@/views/PlaceholderPage.vue')

const routes: RouteRecordRaw[] = [
  { path: '/demo-chat', component: () => import('@/views/demo-chat/index.vue'), meta: { public: true } },
  { path: '/gate', component: () => import('@/views/Gate/index.vue'), meta: { public: true } },
  { path: '/login', redirect: '/gate' },
  {
    path: '/',
    component: SalesLayout,
    redirect: '/workbench',
    children: [
      { path: 'workbench', component: () => import('@/views/workbench/index.vue'), meta: { title: '销售工作台' } },
      { path: 'operations/chat-logs', component: PlaceholderPage, props: { title: '销售对话日志' }, meta: { title: '销售对话日志' } },
      { path: 'operations/handoff', component: PlaceholderPage, props: { title: '人工接管' }, meta: { title: '人工接管' } },
      { path: 'operations/user-profile', component: PlaceholderPage, props: { title: '客户画像' }, meta: { title: '客户画像' } },
      { path: 'knowledge-ops/current-activities', component: () => import('@/views/current-activities/index.vue'), meta: { title: '销售活动' } },
      { path: 'knowledge-ops/wechat-materials', component: () => import('@/views/wechat-materials/index.vue'), meta: { title: '微信素材库' } },
      { path: 'knowledge-ops/knowledge', component: PlaceholderPage, props: { title: '销售知识库' }, meta: { title: '销售知识库' } },
      { path: 'knowledge-ops/templates', component: PlaceholderPage, props: { title: '销售话术' }, meta: { title: '销售话术' } },
      { path: 'knowledge-ops/intent-examples', component: PlaceholderPage, props: { title: '销售意图样本' }, meta: { title: '销售意图样本' } },
      { path: 'sop/unpurchased', component: () => import('@/views/unpurchased-sop/index.vue'), meta: { title: '未购 SOP' } },
      { path: 'settings/model-config', component: PlaceholderPage, props: { title: '模型配置' }, meta: { title: '模型配置' } }
    ]
  },
  { path: '/:pathMatch(.*)*', redirect: '/workbench' }
]

const router = createRouter({ history: createWebHistory(import.meta.env.BASE_URL), routes })

router.beforeEach(async (to) => {
  if (to.meta.public) return true
  try {
    const response = await fetch('/api/gate', { credentials: 'same-origin' })
    const result = await response.json()
    const role = result?.data?.role as GateRole | null
    if (result?.data?.unlocked && (role === 'admin' || role === 'test')) {
      setGateRole(role)
      return true
    }
  } catch {
    // Fall through to the gate page.
  }
  clearGateRole()
  return { path: '/gate', query: { redirect: to.fullPath } }
})

router.afterEach((to) => { document.title = `${String(to.meta.title || '销售 Agent')} - 销售 Agent` })

export default router

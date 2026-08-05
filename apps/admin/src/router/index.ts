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
      { path: 'workbench', component: () => import('@/views/workbench/index.vue'), meta: { title: '小兰工作台' } },
      { path: 'operations/conversation-cases', component: () => import('@/views/conversation-cases/index.vue'), meta: { title: '销售案例库' } },
      { path: 'operations/tags', component: () => import('@/views/tag-management/index.vue'), meta: { title: '客户标签' } },
      { path: 'operations/products', component: () => import('@/views/product-information/index.vue'), meta: { title: '产品信息' } },
      { path: 'operations/care-manuals', component: () => import('@/views/care-manuals/index.vue'), meta: { title: '养护手册' } },
      {
        path: 'knowledge-ops/current-activities',
        name: 'CurrentActivities',
        component: () => import('@/views/current-activities/index.vue'),
        meta: { title: '销售活动' }
      },
      { path: 'settings/handoff', component: () => import('@/views/handoff-settings/index.vue'), meta: { title: '转人工设置' } },
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

router.afterEach((to) => {
  document.title = `${String(to.meta.title || '小兰工作台')} - 萧岚苑销售 Agent`
})

export default router

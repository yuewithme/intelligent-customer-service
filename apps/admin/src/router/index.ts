import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import SalesLayout from '@/layouts/SalesLayout.vue'
import { clearGateRole, setAccount, canViewPage, firstAllowedPage } from '@/utils/gate'
import { libraryApp, libraryBase, libraryPages, libraryTitle, libraryLogin, apiUrl, migratedPages, portalOrigin } from '@/utils/library'

const routes: RouteRecordRaw[] = [
  { path: '/demo-chat', component: () => import('@/views/demo-chat/index.vue'), meta: { public: true } },
  { path: '/gate', component: () => import('@/views/Gate/index.vue'), meta: { public: true } },
  { path: '/login', redirect: '/gate' },
  {
    path: '/',
    component: libraryApp === 'product-knowledge' ? () => import('@/layouts/ProductLibraryLayout.vue') : SalesLayout,
    redirect: libraryApp ? libraryPages[libraryApp][0] : '/workbench',
    children: [
      { path: 'no-access', component: () => import('@/views/accounts/NoAccess.vue'), meta: { title: '暂无页面权限', public: true } },
      { path: 'workbench', component: () => import('@/views/workbench/index.vue'), meta: { title: '小兰工作台' } },
      { path: 'operations/conversation-cases', component: () => import('@/views/conversation-cases/index.vue'), meta: { title: '销售案例库' } },
      { path: 'operations/capability-workbench', component: () => import('@/views/capability-workbench/index.vue'), meta: { title: '能力工作台' } },
      { path: 'operations/tags', component: () => import('@/views/tag-management/index.vue'), meta: { title: '客户标签' } },
      { path: 'operations/products', component: () => import('@/views/product-information/index.vue'), meta: { title: '商品与产品资料' } },
      { path: 'operations/care-manuals', component: () => import('@/views/care-manuals/index.vue'), meta: { title: '养护手册' } },
      {
        path: 'knowledge-ops/current-activities',
        name: 'CurrentActivities',
        component: () => import('@/views/current-activities/index.vue'),
        meta: { title: '销售活动' }
      },
      { path: 'settings/handoff', component: () => import('@/views/handoff-settings/index.vue'), meta: { title: '转人工设置' } }
    ]
  },
  { path: '/:pathMatch(.*)*', redirect: '/workbench' }
]

const router = createRouter({ history: createWebHistory(libraryBase || import.meta.env.BASE_URL), routes })

router.beforeEach(async (to) => {
  const owner = migratedPages[to.path]
  if (!libraryApp && owner) {
    location.replace(`${portalOrigin}${owner}${to.fullPath.slice(to.path.length)}`)
    return false
  }
  if (libraryApp && to.path !== '/no-access' && !libraryPages[libraryApp].includes(to.path)) return '/no-access'
  if (to.meta.public) return true
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), 10000)
  try {
    const response = await fetch(apiUrl('/api/gate'), { credentials: 'same-origin', signal: controller.signal })
    if (response.status === 403) {
      clearGateRole()
      return '/no-access'
    }
    if (!response.ok && response.status !== 401) throw new Error('身份服务暂不可用')
    const result = await response.json()
    const account = result?.data?.account
    if (result?.data?.unlocked && account) {
      setAccount(account)
      if (to.path === '/no-access' || canViewPage(to.path)) return true
      return firstAllowedPage()
    }
  } catch {
    clearGateRole()
    if (libraryApp) return '/no-access'
    return { path: '/gate', query: { redirect: to.fullPath, error: 'connection' } }
  } finally {
    clearTimeout(timer)
  }
  clearGateRole()
  if (libraryApp) { location.replace(libraryLogin()); return false }
  return { path: '/gate', query: { redirect: to.fullPath } }
})

router.afterEach((to) => {
  void import('@/styles/fonts.css').catch(() => undefined)
  document.title = `${String(to.meta.title || '小兰工作台')} - ${libraryApp ? libraryTitle : '萧岚苑销售 Agent'}`
})

export default router

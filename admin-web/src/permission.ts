import router from './router'
import type { RouteRecordRaw } from 'vue-router'
import { getAccessToken, setDefaultAdminToken } from '@/utils/auth'
import { useTitle } from '@/hooks/web/useTitle'
import { useNProgress } from '@/hooks/web/useNProgress'
import { usePageLoading } from '@/hooks/web/usePageLoading'
import { useUserStoreWithOut } from '@/store/modules/user'
import { usePermissionStoreWithOut } from '@/store/modules/permission'
import { parseRouteLocation } from '@/utils/routeParams'

const { start, done } = useNProgress()

const { loadStart, loadDone } = usePageLoading()

// 路由不重定向白名单
const whiteList = [
  '/gate',
  '/login',
  '/auth-redirect',
  '/demo-chat'
]

const gateEnabled = import.meta.env.VITE_GATE_ENABLED !== 'false'

const isGateUnlocked = async () => {
  if (!gateEnabled) return true
  try {
    const response = await fetch('/api/gate', {
      credentials: 'same-origin',
      headers: { Accept: 'application/json' }
    })
    if (!response.ok) return false
    const result = await response.json()
    return Boolean(result?.data?.unlocked)
  } catch {
    return false
  }
}

// 路由加载前
router.beforeEach(async (to, from, next) => {
  start()
  loadStart()
  if (!whiteList.includes(to.path) && !(await isGateUnlocked())) {
    next(`/gate?redirect=${encodeURIComponent(to.fullPath)}`)
    return
  }
  if (to.path === '/gate' && (await isGateUnlocked())) {
    const redirect =
      typeof to.query.redirect === 'string' && to.query.redirect.startsWith('/')
        ? to.query.redirect
        : '/workbench'
    next(parseRouteLocation(redirect))
    return
  }
  if (!getAccessToken()) {
    setDefaultAdminToken()
  }
  if (getAccessToken()) {
    if (to.path === '/login') {
      next({ path: '/workbench' })
    } else {
      const userStore = useUserStoreWithOut()
      const permissionStore = usePermissionStoreWithOut()
      if (!userStore.getIsSetUser) {
        await userStore.setUserInfoAction()
        await permissionStore.generateRoutes()
        permissionStore.getAddRouters.forEach((route) => {
          router.addRoute(route as unknown as RouteRecordRaw) // 动态添加可访问路由表
        })
        const redirectPath = from.query.redirect
        // 修复跳转时不带参数的问题
        const redirect = typeof redirectPath === 'string' ? redirectPath : to.fullPath
        const redirectLocation = parseRouteLocation(redirect)
        const nextData =
          to.fullPath === redirect
            ? { ...to, replace: true }
            : { ...redirectLocation, replace: true }
        next(nextData)
      } else {
        next()
      }
    }
  } else {
    if (whiteList.indexOf(to.path) !== -1) {
      next()
    } else {
      next(`/login?redirect=${encodeURIComponent(to.fullPath)}`) // 否则全部重定向到登录页
    }
  }
})

router.afterEach((to) => {
  useTitle(to?.meta?.title as string)
  done() // 结束Progress
  loadDone()
})

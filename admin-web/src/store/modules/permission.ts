import { defineStore } from 'pinia'
import { store } from '@/store'
import { cloneDeep } from 'lodash-es'
import remainingRouter from '@/router/modules/remaining'
import adminRoutes from '@/router/modules/admin'
import { flatMultiLevelRoutes } from '@/utils/routerHelper'

export interface PermissionState {
  routers: AppRouteRecordRaw[]
  addRouters: AppRouteRecordRaw[]
  menuTabRouters: AppRouteRecordRaw[]
  menuRootPath: string
}

const notFoundRoute: AppRouteRecordRaw = {
  path: '/:path(.*)*',
  component: () => import('@/views/Error/404.vue'),
  name: '404Page',
  meta: {
    hidden: true,
    breadcrumb: false
  }
}

export const usePermissionStore = defineStore('permission', {
  state: (): PermissionState => ({
    routers: [],
    addRouters: [],
    menuTabRouters: [],
    menuRootPath: ''
  }),
  getters: {
    getRouters(): AppRouteRecordRaw[] {
      return this.routers
    },
    getAddRouters(): AppRouteRecordRaw[] {
      return flatMultiLevelRoutes(cloneDeep(this.addRouters))
    },
    getMenuTabRouters(): AppRouteRecordRaw[] {
      return this.menuTabRouters
    },
    getMenuRootPath(): string {
      return this.menuRootPath
    }
  },
  actions: {
    async generateRoutes(): Promise<void> {
      this.addRouters = cloneDeep(adminRoutes).concat([notFoundRoute])
      this.routers = cloneDeep(remainingRouter).concat(cloneDeep(adminRoutes))
    },
    setMenuTabRouters(routers: AppRouteRecordRaw[]): void {
      this.menuTabRouters = routers
    },
    setMenuRootPath(path: string): void {
      this.menuRootPath = path
    }
  },
  persist: false
})

export const usePermissionStoreWithOut = () => {
  return usePermissionStore(store)
}

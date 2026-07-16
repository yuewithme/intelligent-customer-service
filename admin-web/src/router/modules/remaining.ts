import { Layout } from '@/utils/routerHelper'

const remainingRouter: AppRouteRecordRaw[] = [
  {
    path: '/demo-chat',
    component: () => import('@/views/demo-chat/index.vue'),
    name: 'DemoChat',
    meta: { hidden: true, title: '销售 Agent 在线测试', noTagsView: true }
  },
  {
    path: '/demo-admin',
    component: () => import('@/views/demo-admin/index.vue'),
    name: 'DemoAdmin',
    meta: { hidden: true, title: '销售 Agent 测试后台', noTagsView: true }
  },
  {
    path: '/redirect',
    component: Layout,
    name: 'RedirectRoot',
    children: [
      {
        path: '/redirect/:path(.*)',
        name: 'Redirect',
        component: () => import('@/views/Redirect/Redirect.vue'),
        meta: {}
      }
    ],
    meta: {
      hidden: true,
      noTagsView: true
    }
  },
  {
    path: '/',
    component: Layout,
    redirect: '/workbench',
    name: 'Home',
    meta: {
      hidden: true
    }
  },
  {
    path: '/user',
    component: Layout,
    name: 'UserInfo',
    meta: {
      hidden: true
    },
    children: [
      {
        path: 'profile',
        component: () => import('@/views/Profile/Index.vue'),
        name: 'Profile',
        meta: {
          canTo: true,
          hidden: true,
          noTagsView: false,
          icon: 'ep:user',
          title: '个人中心'
        }
      }
    ]
  },
  {
    path: '/gate',
    component: () => import('@/views/Gate/index.vue'),
    name: 'Gate',
    meta: {
      hidden: true,
      title: '访问门禁',
      noTagsView: true
    }
  },
  {
    path: '/login',
    component: () => import('@/views/Login/Login.vue'),
    name: 'Login',
    meta: {
      hidden: true,
      title: '登录',
      noTagsView: true
    }
  },
  {
    path: '/403',
    component: () => import('@/views/Error/403.vue'),
    name: 'NoAccess',
    meta: {
      hidden: true,
      title: '403',
      noTagsView: true
    }
  },
  {
    path: '/404',
    component: () => import('@/views/Error/404.vue'),
    name: 'NoFound',
    meta: {
      hidden: true,
      title: '404',
      noTagsView: true
    }
  },
  {
    path: '/500',
    component: () => import('@/views/Error/500.vue'),
    name: 'Error',
    meta: {
      hidden: true,
      title: '500',
      noTagsView: true
    }
  }
]

export default remainingRouter

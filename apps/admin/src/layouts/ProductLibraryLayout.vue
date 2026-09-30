<template>
  <div class="review-library-shell">
    <a href="#main-content" class="review-skip-link">跳至主要内容</a>
    <header class="review-topbar">
      <a class="review-brand" :href="homeHref" aria-label="产品知识库首页">
        <span class="review-brand-mark"><ElIcon :size="25"><Reading /></ElIcon></span>
        <span>产品知识库</span>
      </a>
      <nav class="review-primary-nav" aria-label="业务导航">
        <a v-if="access?.pages.includes('materials')" href="/product-knowledge/">
          <ElIcon :size="18"><Reading /></ElIcon>产品知识库
        </a>
        <RouterLink v-for="page in navigation" :key="page.to" :to="page.to" :aria-current="route.path === page.to ? 'page' : undefined">
          <ElIcon :size="18"><Reading /></ElIcon>{{ page.title }}
        </RouterLink>
      </nav>
      <div class="review-account-nav">
        <a href="/portal/workspace">统一工作台</a>
        <a v-if="access?.is_platform_admin" href="/portal/admin">管理后台</a>
        <span class="review-identity">{{ identity }}</span>
        <button type="button" :disabled="leaving" @click="logout">{{ leaving ? '正在退出…' : '退出' }}</button>
        <button type="button" class="review-theme-toggle" :aria-label="theme === 'light' ? '切换深色模式' : '切换浅色模式'" @click="toggleTheme">
          <ElIcon :size="19"><Moon v-if="theme === 'light'" /><Sunny v-else /></ElIcon>
        </button>
      </div>
    </header>
    <div class="review-workspace-wrap">
      <div class="review-breadcrumb"><ElIcon :size="15"><Grid /></ElIcon><span>工作空间</span><span>/</span><span>产品知识库</span></div>
      <main id="main-content" class="review-content">
        <ElAlert v-if="error" :title="error" type="error" :closable="false" show-icon class="review-alert" />
        <template v-if="route.path !== '/no-access'">
          <div class="review-page-heading">
            <div>
              <div class="review-eyebrow">PROJECT KNOWLEDGE</div>
              <h1>让经验，成为共同的底气。</h1>
              <p>整理项目知识、销售经验与话术，让每次复盘都有据可依。</p>
            </div>
            <div class="review-heading-aside"><span>知识随项目沉淀</span><span>资料归项目，经验有依据</span></div>
          </div>
          <div class="review-project-context">
            <span class="review-project-symbol"><ElIcon :size="21"><FolderOpened /></ElIcon></span>
            <div><span class="review-field-caption">当前管理项目</span><strong>萧兰苑</strong></div>
            <span class="review-context-note"><i></i>资料仅用于当前项目</span>
          </div>
          <ElAlert v-if="!isAdmin()" title="此页面为观看权限，修改操作由管理员执行" type="info" :closable="false" class="review-alert" />
        </template>
        <RouterView :key="route.path" />
      </main>
      <footer class="review-page-footer">让经验沉淀，让沟通生长</footer>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watchEffect } from 'vue'
import { RouterLink, RouterView, useRoute } from 'vue-router'
import { FolderOpened, Grid, Moon, Reading, Sunny } from '@element-plus/icons-vue'
import { canViewPage, clearGateRole, currentAccount, firstAllowedPage, isAdmin } from '@/utils/gate'
import { libraryBase, libraryLogin } from '@/utils/library'
import '@/styles/product-library-review.css'

interface LibraryAccess {
  role: 'sales' | 'project_admin' | 'global_admin'
  project_name: string
  display_name: string
  pages: string[]
  is_platform_admin: boolean
}

const route = useRoute()
const access = ref<LibraryAccess | null>(null)
const leaving = ref(false)
const error = ref('')
const theme = ref(localStorage.getItem('sales-review-theme') === 'dark' ? 'dark'
  : localStorage.getItem('sales-review-theme') === 'light' ? 'light'
  : matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light')
const navigation = computed(() => [
  { to: '/operations/products', title: '商品与产品资料' },
  { to: '/operations/care-manuals', title: '养护手册' },
  { to: '/knowledge-ops/current-activities', title: '销售活动' }
].filter(page => canViewPage(page.to)))
const homeHref = computed(() => access.value?.pages.includes('materials') ? '/product-knowledge/' : libraryBase + firstAllowedPage())
const identity = computed(() => access.value?.role === 'global_admin' ? '全部项目 · 超级管理员'
  : access.value ? `${access.value.project_name} · ${access.value.display_name} · ${access.value.role === 'sales' ? '员工' : '管理员'}`
  : currentAccount.value?.display_name || '')

watchEffect(() => {
  document.documentElement.classList.add('product-library-review')
  document.documentElement.dataset.theme = theme.value
})
const toggleTheme = () => {
  theme.value = theme.value === 'light' ? 'dark' : 'light'
  localStorage.setItem('sales-review-theme', theme.value)
}
const onStorage = (event: StorageEvent) => {
  if (event.key === 'sales-review-theme' && (event.newValue === 'light' || event.newValue === 'dark')) theme.value = event.newValue
}
const controller = new AbortController()
onMounted(async () => {
  window.addEventListener('storage', onStorage)
  const timer = window.setTimeout(() => controller.abort(), 10000)
  try {
    const response = await fetch('/product-knowledge/api/auth/session', { credentials: 'same-origin', signal: controller.signal })
    if (response.status === 401) { location.replace(libraryLogin()); return }
    if (!response.ok) throw new Error('身份信息加载失败，请刷新后重试。')
    access.value = (await response.json()).access
  } catch {
    if (!disposed) error.value = '身份信息加载失败，请刷新后重试。'
  } finally { window.clearTimeout(timer) }
})
let disposed = false
onUnmounted(() => {
  disposed = true
  controller.abort()
  window.removeEventListener('storage', onStorage)
  document.documentElement.classList.remove('product-library-review')
  delete document.documentElement.dataset.theme
})
const logout = async () => {
  leaving.value = true
  error.value = ''
  try {
    const cookie = document.cookie.split(';').map(value => value.trim()).find(value => value.startsWith('csrftoken='))
    const response = await fetch('/product-knowledge/api/auth/logout', { method: 'POST', credentials: 'same-origin', headers: { 'X-CSRFToken': cookie ? decodeURIComponent(cookie.slice(10)) : '' } })
    if (!response.ok) throw new Error('退出失败，请重试。')
    clearGateRole()
    location.assign('/portal/workspace')
  } catch { error.value = '退出失败，请重试。' }
  finally { leaving.value = false }
}
</script>

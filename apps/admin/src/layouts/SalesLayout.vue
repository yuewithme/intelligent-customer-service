<template>
  <div class="sales-layout">
    <aside class="sidebar">
      <RouterLink class="brand" to="/workbench">
        <span class="brand-mark">SA</span>
        <span><strong>销售 Agent</strong><small>运营管理平台</small></span>
      </RouterLink>
      <nav>
        <section v-for="group in navigation" :key="group.title">
          <p>{{ group.title }}</p>
          <RouterLink v-for="item in group.items" :key="item.to" :to="item.to">
            {{ item.label }}
          </RouterLink>
        </section>
      </nav>
    </aside>
    <div class="page-area">
      <header>
        <div>
          <strong>{{ currentTitle }}</strong>
          <span>销售 Agent 实时运营与策略管理</span>
        </div>
        <div class="operator">
          <span v-if="testMode" class="test-badge">测试模式</span>
          <span>{{ userStore.user.nickname }}</span>
          <button type="button" @click="logout">退出</button>
        </div>
      </header>
      <main><RouterView /></main>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink, RouterView, useRoute, useRouter } from 'vue-router'
import { useUserStore } from '@/store/modules/user'
import { clearAccessToken } from '@/utils/auth'
import { clearGateRole, isTestGate } from '@/utils/gate'

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()
const currentTitle = computed(() => String(route.meta.title || '销售工作台'))
const testMode = isTestGate()

const navigation = [
  { title: '销售执行', items: [
    { label: '销售工作台', to: '/workbench' },
    { label: '首单销售流程', to: '/sales-flow' }
  ] },
  { title: '销售运营', items: [
    { label: '意图标注日志', to: '/operations/chat-logs' },
    { label: '影子决策评测', to: '/operations/reply-shadows' },
    { label: '标签管理', to: '/operations/tags' },
    { label: '产品信息', to: '/operations/products' },
    { label: '养护手册', to: '/operations/care-manuals' }
  ] },
  { title: '策略与知识', items: [
    { label: '销售活动', to: '/knowledge-ops/current-activities' }
  ] },
  { title: 'SOP流程', items: [
    { label: '未购SOP', to: '/sop/unpurchased' },
    { label: '服务SOP', to: '/sop/service' }
  ] },
  { title: '系统', items: [
    { label: '转人工设置', to: '/settings/handoff' },
    { label: '模型配置', to: '/settings/model-config' }
  ] }
]

const logout = async () => {
  await fetch('/api/gate', { method: 'DELETE', credentials: 'same-origin' })
  clearGateRole()
  clearAccessToken()
  userStore.reset()
  void router.replace('/gate')
}
</script>

<style scoped>
.sales-layout { display: grid; grid-template-columns: 232px minmax(0, 1fr); min-height: 100vh; }
.sidebar { position: sticky; top: 0; height: 100vh; padding: 20px 14px; color: #d7e7e1; background: #123f33; }
.brand { display: flex; align-items: center; gap: 12px; padding: 4px 8px 22px; color: #fff; text-decoration: none; }
.brand-mark { display: grid; width: 38px; height: 38px; place-items: center; font-weight: 800; background: #35a37a; border-radius: 11px; }
.brand strong, .brand small { display: block; }
.brand small { margin-top: 3px; color: #9bc2b4; font-size: 11px; }
nav section { margin: 15px 0 22px; }
nav p { padding: 0 12px; margin: 0 0 7px; color: #80ab9b; font-size: 11px; letter-spacing: .12em; }
nav a { display: block; padding: 10px 12px; margin: 3px 0; color: #cce0d8; text-decoration: none; border-radius: 8px; }
nav a:hover, nav a.router-link-active { color: #fff; background: #23634f; }
.page-area { min-width: 0; }
header { display: flex; align-items: center; justify-content: space-between; min-height: 64px; padding: 0 22px; background: #fff; border-bottom: 1px solid #e5e7eb; }
header strong, header span { display: block; }
header span { margin-top: 3px; color: #84918c; font-size: 12px; }
.operator { display: flex; align-items: center; gap: 12px; }
.operator span { color: #33443e; font-size: 14px; }
.operator .test-badge { padding: 4px 9px; color: #9a4f00; font-weight: 700; background: #fff2d8; border-radius: 999px; }
.operator button { padding: 6px 10px; color: #50645d; cursor: pointer; background: transparent; border: 1px solid #cfdad6; border-radius: 7px; }
main { min-width: 0; }
@media (max-width: 820px) { .sales-layout { grid-template-columns: 1fr; } .sidebar { position: static; width: 100%; height: auto; } nav { display: none; } }
</style>

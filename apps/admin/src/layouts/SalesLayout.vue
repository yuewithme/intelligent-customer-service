<template>
  <div class="sales-layout">
    <button
      v-if="mobileNavOpen"
      class="nav-backdrop"
      type="button"
      aria-label="关闭导航"
      @click="closeMobileNav"
    ></button>
    <aside class="sidebar" :class="{ open: mobileNavOpen }">
      <RouterLink class="brand" to="/workbench" @click="closeMobileNav">
        <span class="brand-mark">兰</span>
        <span><strong>小兰 Agent</strong><small>萧岚苑销售运营台</small></span>
      </RouterLink>
      <button class="nav-close" type="button" aria-label="关闭导航" @click="closeMobileNav">
        ×
      </button>
      <div v-if="showTenantSwitcher" class="mobile-tenant-switcher">
        <span>小兰微信</span>
        <ElSelect
          :model-value="tenantStore.selectedTenantId"
          :loading="tenantStore.loading"
          placeholder="暂无可切换的微信账号"
          @change="switchTenant"
        >
          <ElOption
            v-for="tenant in tenantStore.tenants"
            :key="tenant.tenant_id"
            :label="tenantOptionLabel(tenant)"
            :value="tenant.tenant_id"
          />
        </ElSelect>
      </div>
      <nav>
        <section v-for="group in navigation" :key="group.title">
          <p>{{ group.title }}</p>
          <RouterLink
            v-for="item in group.items"
            :key="item.to"
            :to="item.to"
            @click="closeMobileNav"
          >
            {{ item.label }}
          </RouterLink>
        </section>
      </nav>
      <div class="mobile-operator">
        <span v-if="testMode" class="test-badge">测试模式</span>
        <span>{{ userStore.user.nickname }}</span>
        <button type="button" @click="logout">退出登录</button>
      </div>
    </aside>
    <div class="page-area">
      <header>
        <button class="nav-trigger" type="button" aria-label="打开导航" @click="openMobileNav">
          <span></span><span></span><span></span>
        </button>
        <div class="page-title">
          <strong>{{ currentTitle }}</strong>
          <span>小兰自主销售 Agent 的实时运营与人工协作</span>
        </div>
        <div v-if="showTenantSwitcher" class="tenant-switcher">
          <span class="tenant-label">小兰微信</span>
          <ElSelect
            :model-value="tenantStore.selectedTenantId"
            :loading="tenantStore.loading"
            placeholder="暂无可切换的微信账号"
            @change="switchTenant"
          >
            <ElOption
              v-for="tenant in tenantStore.tenants"
              :key="tenant.tenant_id"
              :label="tenantOptionLabel(tenant)"
              :value="tenant.tenant_id"
            >
              <div class="tenant-option">
                <span>{{ tenant.display_name || '微信账号' }}</span>
                <small>{{ tenant.wc_id }} · {{ tenant.conversation_count }} 个会话</small>
              </div>
            </ElOption>
          </ElSelect>
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
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink, RouterView, useRoute, useRouter } from 'vue-router'
import { useUserStore } from '@/store/modules/user'
import { clearGateRole, isTestGate } from '@/utils/gate'
import { useMessageTenantStore } from '@/store/modules/messageTenant'
import type { ConversationTenant } from '@/api/admin/conversations'

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()
const currentTitle = computed(() => String(route.meta.title || '小兰工作台'))
const testMode = isTestGate()
const tenantStore = useMessageTenantStore()
const showTenantSwitcher = computed(() => !testMode && route.path === '/workbench')
const mobileNavOpen = ref(false)

const openMobileNav = () => {
  mobileNavOpen.value = true
}

const closeMobileNav = () => {
  mobileNavOpen.value = false
}

const tenantOptionLabel = (tenant: ConversationTenant) =>
  tenant.display_name ? `${tenant.display_name}（${tenant.wc_id}）` : tenant.wc_id

const switchTenant = (tenantId: string) => {
  tenantStore.selectTenant(tenantId)
}

onMounted(() => {
  if (showTenantSwitcher.value) void tenantStore.loadTenants()
})

watch(showTenantSwitcher, (visible) => {
  if (visible) void tenantStore.loadTenants()
})

const navigation = [
  {
    title: '销售执行',
    items: [{ label: '小兰工作台', to: '/workbench' }]
  },
  {
    title: '智能编排',
    items: [
      { label: '能力工作台', to: '/operations/capability-workbench' }
    ]
  },
  {
    title: '销售资产',
    items: [
      { label: '销售案例库', to: '/operations/conversation-cases' },
      { label: '客户标签', to: '/operations/tags' },
      { label: '产品信息', to: '/operations/products' },
      { label: '养护手册', to: '/operations/care-manuals' },
      { label: '销售活动', to: '/knowledge-ops/current-activities' }
    ]
  },
  {
    title: '系统',
    items: [
      { label: '转人工设置', to: '/settings/handoff' },
      { label: '模型配置', to: '/settings/model-config' }
    ]
  }
]

const logout = async () => {
  closeMobileNav()
  await fetch('/api/gate', { method: 'DELETE', credentials: 'same-origin' })
  clearGateRole()
  userStore.reset()
  void router.replace('/gate')
}
</script>

<style scoped>
.sales-layout { display: grid; grid-template-columns: 232px minmax(0, 1fr); min-height: 100vh; }
.sidebar { position: sticky; top: 0; height: 100vh; padding: 20px 14px; color: #d7e7e1; background: #123f33; }
.nav-backdrop, .nav-close, .nav-trigger, .mobile-tenant-switcher, .mobile-operator { display: none; }
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
.tenant-switcher { display: flex; align-items: center; gap: 10px; width: min(440px, 38vw); }
.tenant-switcher .tenant-label { flex: 0 0 auto; margin: 0; color: #50645d; font-size: 13px; }
.tenant-switcher :deep(.el-select) { flex: 1; }
.tenant-option { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.tenant-option small { color: #84918c; font-size: 12px; }
.operator { display: flex; align-items: center; gap: 12px; }
.operator span { color: #33443e; font-size: 14px; }
.operator .test-badge { padding: 4px 9px; color: #9a4f00; font-weight: 700; background: #fff2d8; border-radius: 999px; }
.operator button { padding: 6px 10px; color: #50645d; cursor: pointer; background: transparent; border: 1px solid #cfdad6; border-radius: 7px; }
main { min-width: 0; }
@media (max-width: 1100px) { .tenant-switcher { width: min(340px, 34vw); } .tenant-label { display: none; } }
@media (max-width: 820px), (hover: none) and (pointer: coarse) {
  .sales-layout { display: block; min-height: 100dvh; }
  .sidebar {
    position: fixed;
    top: 0;
    bottom: 0;
    left: 0;
    z-index: 40;
    width: min(82vw, 320px);
    height: 100dvh;
    padding: 18px 14px max(18px, env(safe-area-inset-bottom));
    overflow-y: auto;
    box-shadow: 12px 0 36px rgb(2 20 14 / 26%);
    transform: translateX(-105%);
    transition: transform .2s ease;
  }
  .sidebar.open { transform: translateX(0); }
  .nav-backdrop {
    position: fixed;
    inset: 0;
    z-index: 30;
    display: block;
    padding: 0;
    background: rgb(15 23 42 / 48%);
    border: 0;
  }
  .nav-close {
    position: absolute;
    top: 14px;
    right: 12px;
    display: grid;
    width: 36px;
    height: 36px;
    padding: 0;
    place-items: center;
    color: #d7e7e1;
    font-size: 26px;
    line-height: 1;
    cursor: pointer;
    background: transparent;
    border: 0;
  }
  .brand { padding-right: 38px; padding-bottom: 14px; }
  nav { display: block; }
  nav section { margin: 12px 0 16px; }
  nav a { min-height: 44px; padding: 12px; }
  .mobile-tenant-switcher {
    display: grid;
    gap: 7px;
    padding: 12px;
    margin-bottom: 8px;
    background: rgb(255 255 255 / 7%);
    border-radius: 9px;
  }
  .mobile-tenant-switcher > span { color: #b7d4ca; font-size: 12px; }
  .mobile-operator {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 14px 10px 0;
    margin-top: 12px;
    border-top: 1px solid rgb(255 255 255 / 12%);
  }
  .mobile-operator > span { color: #d7e7e1; font-size: 13px; }
  .mobile-operator .test-badge { padding: 3px 7px; color: #ffd99c; background: rgb(255 207 128 / 12%); border-radius: 999px; }
  .mobile-operator button { margin-left: auto; padding: 7px 9px; color: #d7e7e1; background: transparent; border: 1px solid rgb(255 255 255 / 22%); border-radius: 7px; }
  header {
    position: sticky;
    top: 0;
    z-index: 20;
    justify-content: flex-start;
    min-height: 56px;
    padding: 0 12px;
  }
  .nav-trigger {
    display: grid;
    flex: 0 0 40px;
    width: 40px;
    height: 40px;
    padding: 10px 8px;
    margin-right: 8px;
    align-content: space-around;
    cursor: pointer;
    background: transparent;
    border: 0;
    border-radius: 8px;
  }
  .nav-trigger span { height: 2px; margin: 0; background: #36534a; border-radius: 999px; }
  .page-title { min-width: 0; }
  .page-title strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .page-title > span { display: none; }
  header > .tenant-switcher, header > .operator { display: none; }
}
</style>

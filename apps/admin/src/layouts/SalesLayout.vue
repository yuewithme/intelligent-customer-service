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
      <RouterLink class="brand" :to="firstAllowedPage()" @click="closeMobileNav">
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
            <ElIcon aria-hidden="true"><component :is="item.icon" /></ElIcon>
            <span>{{ item.label }}</span>
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
      <main>
        <ElAlert v-if="!isAdmin() && route.path !== '/workbench' && route.path !== '/no-access'" title="此页面为观看权限，修改操作由管理员执行" type="info" :closable="false" />
        <RouterView :key="route.path" />
      </main>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink, RouterView, useRoute, useRouter } from 'vue-router'
import { ChatDotRound, Connection, Collection, PriceTag, Goods, Reading, Calendar, Service, Setting, User } from '@element-plus/icons-vue'
import { useUserStore } from '@/store/modules/user'
import { clearGateRole, isTestGate, canViewPage, firstAllowedPage, isAdmin } from '@/utils/gate'
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

const allNavigation = [
  {
    title: '销售执行',
    items: [{ label: '小兰工作台', to: '/workbench', icon: ChatDotRound }]
  },
  {
    title: '智能编排',
    items: [
      { label: '能力工作台', to: '/operations/capability-workbench', icon: Connection }
    ]
  },
  {
    title: '销售资产',
    items: [
      { label: '销售案例库', to: '/operations/conversation-cases', icon: Collection },
      { label: '客户标签', to: '/operations/tags', icon: PriceTag },
      { label: '产品信息', to: '/operations/products', icon: Goods },
      { label: '养护手册', to: '/operations/care-manuals', icon: Reading },
      { label: '销售活动', to: '/knowledge-ops/current-activities', icon: Calendar }
    ]
  },
  {
    title: '系统',
    items: [
      { label: '转人工设置', to: '/settings/handoff', icon: Service },
      { label: '模型配置', to: '/settings/model-config', icon: Setting },
      { label: '账号与权限', to: '/settings/accounts', icon: User }
    ]
  }
]

const navigation = computed(() => allNavigation.map(group => ({ ...group, items: group.items.filter(item => canViewPage(item.to)) })).filter(group => group.items.length))

const logout = async () => {
  closeMobileNav()
  await fetch('/api/gate', { method: 'DELETE', credentials: 'same-origin' })
  clearGateRole()
  userStore.reset()
  void router.replace('/gate')
}
</script>

<style scoped>
.sales-layout { display: grid; grid-template-columns: 208px minmax(0, 1fr); min-height: 100dvh; }
.sidebar { position: sticky; top: 0; height: 100dvh; padding: 24px 12px; overflow-y: auto; color: var(--app-text); background: #fcfdfc; border-right: 1px solid var(--app-border); }
.nav-backdrop, .nav-close, .nav-trigger, .mobile-tenant-switcher, .mobile-operator { display: none; }
.brand { display: flex; align-items: center; gap: 10px; padding: 0 8px 24px; color: var(--app-text); text-decoration: none; }
.brand-mark { display: grid; flex: 0 0 36px; width: 36px; height: 36px; place-items: center; color: #fff; font-size: 18px; font-weight: 600; background: var(--app-accent); border-radius: 10px; }
.brand strong, .brand small { display: block; }
.brand strong { font-size: 16px; font-weight: 600; }
.brand small { margin-top: 2px; color: var(--app-text-secondary); font-size: 11px; }
nav section { margin: 10px 0 24px; }
nav p { padding: 0 12px; margin: 0 0 8px; color: var(--app-text-secondary); font-size: 11px; }
nav a { display: flex; align-items: center; gap: 10px; min-height: 42px; padding: 9px 12px; margin: 4px 0; color: #52685c; font-size: 14px; text-decoration: none; border-radius: 8px; transition: background .15s ease, color .15s ease; }
nav a .el-icon { font-size: 18px; }
nav a:hover { color: var(--app-text); background: var(--app-surface-soft); }
nav a.router-link-active { color: var(--app-accent); font-weight: 600; background: var(--app-accent-soft); }
.page-area { min-width: 0; }
header { display: flex; align-items: center; justify-content: space-between; gap: 24px; height: var(--app-header-height); padding: 0 32px; background: var(--app-surface); border-bottom: 1px solid var(--app-border); }
.page-title { min-width: 0; }
.page-title strong, .page-title > span { display: block; }
.page-title strong { font-size: 15px; font-weight: 600; }
.page-title > span { margin-top: 3px; color: var(--app-text-secondary); font-size: 12px; }
.tenant-switcher { display: flex; align-items: center; gap: 10px; width: min(380px, 34vw); margin-left: auto; }
.tenant-switcher .tenant-label { flex: 0 0 auto; margin: 0; color: #50645d; font-size: 13px; }
.tenant-switcher :deep(.el-select) { flex: 1; }
.tenant-option { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.tenant-option small { color: #84918c; font-size: 12px; }
.operator { display: flex; flex-shrink: 0; align-items: center; gap: 12px; }
.operator span { color: #33443e; font-size: 14px; }
.operator .test-badge { padding: 4px 9px; color: #9a4f00; font-weight: 700; background: #fff2d8; border-radius: 999px; }
.operator button { padding: 6px 10px; color: var(--app-text-secondary); cursor: pointer; background: transparent; border: 1px solid var(--app-border); border-radius: 8px; }
.operator button:hover { color: var(--app-accent); background: var(--app-accent-soft); }
main { min-width: 0; }
@media (max-width: 1280px) { header { padding: 0 24px; gap: 16px; } .page-title > span { display: none; } }
@media (max-width: 1100px) { .sales-layout { grid-template-columns: 188px minmax(0, 1fr); } .tenant-switcher { width: min(320px, 34vw); } .tenant-label { display: none; } }
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
    box-shadow: 12px 0 36px rgb(36 59 50 / 12%);
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
    color: var(--app-text-secondary);
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
    background: var(--app-surface-soft);
    border-radius: 8px;
  }
  .mobile-tenant-switcher > span { color: var(--app-text-secondary); font-size: 12px; }
  .mobile-operator {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 14px 10px 0;
    margin-top: 12px;
    border-top: 1px solid var(--app-border);
  }
  .mobile-operator > span { color: var(--app-text-secondary); font-size: 13px; }
  .mobile-operator .test-badge { padding: 3px 7px; color: var(--el-color-warning); background: var(--el-color-warning-light-9); border-radius: 6px; }
  .mobile-operator button { margin-left: auto; padding: 7px 9px; color: var(--app-text-secondary); background: transparent; border: 1px solid var(--app-border); border-radius: 8px; }
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

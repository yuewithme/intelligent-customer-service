<template>
  <main class="gate-page">
    <section class="gate-card">
      <h1>正在前往统一平台</h1>
      <p>使用平台账号登录后，将返回销售 Agent。应用权限由平台管理员分配。</p>
      <a :href="loginUrl">继续登录</a>
    </section>
  </main>
</template>

<script setup lang="ts">
import { onMounted } from 'vue'
import { useRoute } from 'vue-router'
const route = useRoute()
const target = typeof route.query.redirect === 'string' ? route.query.redirect : '/workbench'
const loginUrl = `/api/gate/login?redirect=${encodeURIComponent(target)}`
onMounted(() => window.location.replace(loginUrl))
</script>

<style scoped>
.gate-page { display: grid; min-height: 100dvh; padding: 24px; place-items: center; background: var(--app-background); }
.gate-card { width: min(440px, 100%); padding: 32px; background: var(--app-surface); border: 1px solid var(--app-border); border-radius: 12px; }
h1 { font-size: 24px; } p { color: var(--app-text-secondary); line-height: 1.8; } a { color: var(--app-accent); }
</style>

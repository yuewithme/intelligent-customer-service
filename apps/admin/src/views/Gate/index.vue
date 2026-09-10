<template>
  <main class="gate-page">
    <form class="gate-card" @submit.prevent="unlock">
      <div class="brand"><span>SA</span><strong>销售 Agent 后台</strong></div>
      <h1>账号登录</h1>
      <p>请使用管理员分配的账号和密码登录。</p>
      <ElInput v-model="username" autofocus autocomplete="username" placeholder="账号" size="large" maxlength="64" />
      <ElInput v-model="password" autocomplete="current-password" placeholder="密码" maxlength="256" show-password size="large" type="password" />
      <ElButton :loading="loading" native-type="submit" size="large" type="primary">进入后台</ElButton>
    </form>
  </main>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { setAccount, canViewPage, firstAllowedPage } from '@/utils/gate'

const route = useRoute()
const username = ref('')
const password = ref('')
const loading = ref(false)

const unlock = async () => {
  if (!username.value.trim() || !password.value) return ElMessage.warning('请输入账号和密码')
  loading.value = true
  try {
    const response = await fetch('/api/gate', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: username.value.trim(), password: password.value })
    })
    const result = await response.json()
    if (!response.ok) return ElMessage.error(result.message || '登录失败')
    if (!result?.data?.account) return ElMessage.error('登录信息无效')
    setAccount(result.data.account)
    const requested = typeof route.query.redirect === 'string' && route.query.redirect.startsWith('/')
      ? route.query.redirect : firstAllowedPage()
    const target = new URL(requested, window.location.origin)
    const redirect = target.origin === window.location.origin && canViewPage(target.pathname)
      ? `${target.pathname}${target.search}${target.hash}` : firstAllowedPage()
    window.location.replace(redirect)
  } catch { ElMessage.error('登录失败，请检查网络后重试') } finally { loading.value = false }
}
</script>

<style scoped>
.gate-page { display: grid; min-height: 100vh; padding: 24px; place-items: center; background: #eef5f2; }
.gate-card { display: grid; width: min(410px, 100%); gap: 18px; padding: 38px; background: #fff; border: 1px solid #dce7e3; border-radius: 18px; box-shadow: 0 22px 70px rgb(18 63 51 / 12%); }
.brand { display: flex; align-items: center; gap: 11px; }
.brand span { display: grid; width: 40px; height: 40px; place-items: center; color: #fff; font-weight: 800; background: #207457; border-radius: 11px; }
h1 { margin: 8px 0 -10px; } p { margin: 0; color: #6d7d77; }
@media (max-width: 640px) { .gate-page { min-height: 100dvh; padding: 14px; } .gate-card { gap: 16px; padding: 24px; border-radius: 14px; } }
</style>

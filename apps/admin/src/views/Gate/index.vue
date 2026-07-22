<template>
  <main class="gate-page">
    <form class="gate-card" @submit.prevent="unlock">
      <div class="brand"><span>SA</span><strong>销售 Agent 后台</strong></div>
      <h1>访问验证</h1>
      <p>请输入演示后台访问密码。</p>
      <ElInput v-model="password" autofocus placeholder="访问密码" show-password size="large" type="password" />
      <ElButton :loading="loading" native-type="submit" size="large" type="primary">进入后台</ElButton>
    </form>
  </main>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { setGateRole, type GateRole } from '@/utils/gate'

const route = useRoute()
const password = ref('')
const loading = ref(false)

const unlock = async () => {
  if (!password.value.trim()) return ElMessage.warning('请输入访问密码')
  loading.value = true
  try {
    const response = await fetch('/api/gate', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password: password.value })
    })
    if (!response.ok) return ElMessage.error('访问密码不正确')
    const result = await response.json()
    const role = result?.data?.role as GateRole
    if (role !== 'admin' && role !== 'test') return ElMessage.error('门禁身份无效')
    setGateRole(role)
    const redirect = typeof route.query.redirect === 'string' && route.query.redirect.startsWith('/')
      ? route.query.redirect : '/workbench'
    window.location.replace(redirect)
  } finally { loading.value = false }
}
</script>

<style scoped>
.gate-page { display: grid; min-height: 100vh; padding: 24px; place-items: center; background: #eef5f2; }
.gate-card { display: grid; width: min(410px, 100%); gap: 18px; padding: 38px; background: #fff; border: 1px solid #dce7e3; border-radius: 18px; box-shadow: 0 22px 70px rgb(18 63 51 / 12%); }
.brand { display: flex; align-items: center; gap: 11px; }
.brand span { display: grid; width: 40px; height: 40px; place-items: center; color: #fff; font-weight: 800; background: #207457; border-radius: 11px; }
h1 { margin: 8px 0 -10px; } p { margin: 0; color: #6d7d77; }
</style>

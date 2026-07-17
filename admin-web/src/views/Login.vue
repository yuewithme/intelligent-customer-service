<template>
  <main class="login-page">
    <form class="login-card" @submit.prevent="submit">
      <div class="logo">SA</div>
      <p class="eyebrow">SALES AGENT PLATFORM</p>
      <h1>登录销售 Agent 后台</h1>
      <p class="intro">输入管理员标识和 API Key，进入销售运营工作台。</p>
      <label>管理员标识<ElInput v-model="name" size="large" /></label>
      <label>API Key<ElInput v-model="token" size="large" show-password type="password" /></label>
      <ElButton native-type="submit" size="large" type="primary">进入后台</ElButton>
    </form>
  </main>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { setAccessToken } from '@/utils/auth'
import { useUserStore } from '@/store/modules/user'

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()
const name = ref('管理员')
const token = ref('')

const submit = () => {
  if (!token.value.trim()) return ElMessage.warning('请输入 API Key')
  setAccessToken(token.value.trim())
  userStore.setNickname(name.value.trim() || '管理员')
  const redirect = typeof route.query.redirect === 'string' ? route.query.redirect : '/workbench'
  void router.replace(redirect)
}
</script>

<style scoped>
.login-page { display: grid; min-height: 100vh; padding: 24px; place-items: center; background: radial-gradient(circle at 15% 0, #cde9df, transparent 38%), #edf3f1; }
.login-card { display: grid; width: min(420px, 100%); gap: 18px; padding: 38px; background: #fff; border: 1px solid #dce7e3; border-radius: 18px; box-shadow: 0 24px 70px rgb(18 63 51 / 12%); }
.logo { display: grid; width: 46px; height: 46px; place-items: center; color: #fff; font-weight: 800; background: #1f7559; border-radius: 13px; }
.eyebrow { margin: 0; color: #2c8064; font-size: 11px; font-weight: 800; letter-spacing: .13em; }
h1 { margin: -8px 0 0; font-size: 27px; } .intro { margin: -8px 0 4px; color: #66756f; line-height: 1.6; }
label { display: grid; gap: 7px; color: #344740; font-size: 14px; }
</style>
